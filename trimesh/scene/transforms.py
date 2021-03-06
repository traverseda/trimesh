import numpy as np
import time

import networkx as nx

from .. import util
from .. import transformations


class TransformForest:

    def __init__(self, base_frame='world'):
        self.transforms = EnforcedForest()
        self.base_frame = base_frame

        self._paths = {}
        self._updated = time.time()

        self._cache = util.Cache(id_function=self.md5)

    def update(self,
               frame_to,
               frame_from=None,
               **kwargs):
        '''
        Update a transform in the tree.

        Parameters
        ---------
        frame_from: hashable object, usually a string (eg 'world').
                    If left as None it will be set to self.base_frame
        frame_to:   hashable object, usually a string (eg 'mesh_0')

        Additional kwargs (can be used in combinations)
        ---------
        matrix:      (4,4) array
        quaternion:  (4) quaternion
        axis:        (3) array
        angle:       float, radians
        translation: (3) array

        geometry: Geometry object name
        '''
        if frame_from is None:
            frame_from = self.base_frame
        matrix = kwargs_to_matrix(**kwargs)

        attr = {'matrix': matrix,
                'time': time.time()}

        if 'geometry' in kwargs:
            attr['geometry'] = kwargs['geometry']

        changed = self.transforms.add_edge(frame_from,
                                           frame_to,
                                           **attr)
        if 'geometry' in kwargs:
            nx.set_node_attributes(self.transforms,
                                   name='geometry',
                                   values={frame_to: kwargs['geometry']})
        if changed:
            self._paths = {}
        self._updated = time.time()

    def md5(self):
        '''
        MD5 of transforms.

        Currently only hashing update time.
        '''
        result = str(int(self._updated * 1000))
        return result

    def to_flattened(self, base_frame=None):
        '''
        Export the current transform graph as a flattened
        '''
        if base_frame is None:
            base_frame = self.base_frame

        flat = {}
        for node in self.nodes:
            if node == base_frame:
                continue
            transform, geometry = self.get(frame_to=node,
                                           frame_from=base_frame)
            flat[node] = {'transform': transform.tolist(),
                          'geometry': geometry}
        return flat

    def to_edgelist(self):
        '''
        Export the current transforms as a list of edge tuples, with
        each tuple having the format:
        (node_a, node_b, {metadata})

        Returns
        -------
        edgelist: (n,) list of tuples
        '''
        # wrapped in a list for nx 2.0
        export = list(nx.to_edgelist(self.transforms))
        for e in export:
            e[2]['matrix'] = np.array(e[2]['matrix']).tolist()
        return export

    def load(self, edgelist):
        for edge in edgelist:
            self.transforms.add_edge(edge[0], edge[1], **edge[2])

    @util.cache_decorator
    def nodes(self):
        return np.array(self.transforms.nodes())

    @util.cache_decorator
    def nodes_geometry(self):
        nodes = [i for i in self.nodes if 'geometry' in self.transforms.node[i]]
        return np.array(nodes)


    def get(self,
            frame_to,
            frame_from=None):
        '''
        Get the transform from one frame to another, assuming they are connected
        in the transform tree.

        If the frames are not connected a NetworkXNoPath error will be raised.

        Parameters
        ---------
        frame_from: hashable object, usually a string (eg 'world').
                    If left as None it will be set to self.base_frame
        frame_to:   hashable object, usually a string (eg 'mesh_0')

        Returns
        ---------
        transform:  (4,4) homogenous transformation matrix
        '''

        if frame_from is None:
            frame_from = self.base_frame

        cache_key = str(frame_from) + ':' + str(frame_to)
        cached = self._cache[cache_key]
        if cached is not None:
            return cached

        transform = np.eye(4)
        path = self._get_path(frame_from, frame_to)

        for i in range(len(path) - 1):
            data, direction = self.transforms.get_edge_data_direction(
                path[i], path[i + 1])
            matrix = data['matrix']
            if direction < 0:
                matrix = np.linalg.inv(matrix)
            transform = np.dot(transform, matrix)

        geometry = None
        if 'geometry' in self.transforms.node[frame_to]:
            geometry = self.transforms.node[frame_to]['geometry']

        self._cache[cache_key] = (transform, geometry)

        return transform, geometry

    def show(self):
        '''
        Plot the graph layout of the scene.
        '''
        import matplotlib.pyplot as plt
        nx.draw(self.transforms, with_labels=True)
        plt.show()

    def to_svg(self):
        '''
        '''
        from ..graph import graph_to_svg
        return graph_to_svg(self.transforms)

    def __contains__(self, key):
        return key in self.transforms.node

    def __getitem__(self, key):
        return self.get(key)

    def __setitem__(self, key, value):
        value = np.asanyarray(value)
        if value.shape != (4, 4):
            raise ValueError('Matrix must be specified!')
        return self.update(key, matrix=value)

    def clear(self):
        self.transforms = EnforcedForest()
        self._paths = {}
        self._updated = time.time()

    def _get_path(self,
                  frame_from,
                  frame_to):
        '''
        Find a path between two frames, either from cached paths or
        from the transform graph.

        Parameters
        ---------
        frame_from: a frame key, usually a string
                    eg, 'world'
        frame_to:   a frame key, usually a string
                    eg, 'mesh_0'

        Returns
        ----------
        path: (n) list of frame keys
              eg, ['mesh_finger', 'mesh_hand', 'world']
        '''
        key = (frame_from, frame_to)
        if not (key in self._paths):
            path = self.transforms.shortest_path_undirected(frame_from,
                                                            frame_to)
            self._paths[key] = path
        return self._paths[key]


class EnforcedForest(nx.DiGraph):
    '''
    A subclass of networkx.DiGraph that will raise an error if an
    edge is added which would make the DiGraph not a forest or tree.
    '''
    def __init__(self, *args, **kwargs):
        self.flags = {'strict': False,
                      'assert_forest': False}

        for k, v in self.flags.items():
            if k in kwargs:
                self.flags[k] = bool(kwargs[k])
                kwargs.pop(k, None)

        super(self.__class__, self).__init__(*args, **kwargs)
        # keep a second parallel but undirected copy of the graph
        # all of the networkx methods for turning a directed graph
        # into an undirected graph are quite slow, so we do minor bookkeeping
        self._undirected = nx.Graph()

    def add_edge(self, u, v, *args, **kwargs):
        changed = False
        if u == v:
            if self.flags['strict']:
                raise ValueError('Edge must be between two unique nodes!')
            return changed
        if self._undirected.has_edge(u, v):
            self.remove_edges_from([[u, v], [v, u]])
        elif len(self.nodes()) > 0:
            try:
                path = nx.shortest_path(self._undirected, u, v)
                if self.flags['strict']:
                    raise ValueError(
                        'Multiple edge path exists between nodes!')
                self.disconnect_path(path)
                changed = True
            except (nx.NetworkXError, nx.NetworkXNoPath, nx.NetworkXException):
                pass
        self._undirected.add_edge(u, v)
        super(self.__class__, self).add_edge(u, v, *args, **kwargs)

        if self.flags['assert_forest']:
            # this is quite slow but makes very sure structure is correct
            # so is mainly used for testing
            assert nx.is_forest(nx.Graph(self))

        return changed

    def add_edges_from(self, *args, **kwargs):
        raise ValueError('EnforcedTree requires add_edge method to be used!')

    def add_path(self, *args, **kwargs):
        raise ValueError('EnforcedTree requires add_edge method to be used!')

    def remove_edge(self, *args, **kwargs):
        super(self.__class__, self).remove_edge(*args, **kwargs)
        self._undirected.remove_edge(*args, **kwargs)

    def remove_edges_from(self, *args, **kwargs):
        super(self.__class__, self).remove_edges_from(*args, **kwargs)
        self._undirected.remove_edges_from(*args, **kwargs)

    def disconnect_path(self, path):
        ebunch = np.array([[path[0], path[1]]])
        ebunch = np.vstack((ebunch, np.fliplr(ebunch)))
        self.remove_edges_from(ebunch)

    def shortest_path_undirected(self, u, v):
        path = nx.shortest_path(self._undirected, u, v)
        return path

    def get_edge_data_direction(self, u, v):
        if self.has_edge(u, v):
            direction = 1
        elif self.has_edge(v, u):
            direction = -1
        else:
            raise ValueError('Edge doesnt exist!')
        data = self.get_edge_data(*[u, v][::direction])
        return data, direction


def path_to_edges(path):
    '''
    Turn an (n) path into a (2(n-1)) set of edges
    '''
    return np.column_stack((path, path)).reshape(-1)[1:-1].reshape((-1, 2))


def kwargs_to_matrix(**kwargs):
    '''
    Turn a set of keyword arguments into a transformation matrix.
    '''
    matrix = np.eye(4)
    if 'matrix' in kwargs:
        # a matrix takes precedence over other options
        matrix = kwargs['matrix']
    elif 'quaternion' in kwargs:
        matrix = transformations.quaternion_matrix(kwargs['quaternion'])
    elif ('axis' in kwargs) and ('angle' in kwargs):
        matrix = transformations.rotation_matrix(kwargs['angle'],
                                                 kwargs['axis'])
    else:
        raise ValueError('Couldn\'t update transform!')

    if 'translation' in kwargs:
        # translation can be used in conjunction with any of the methods of
        # specifying transforms. In the case a matrix and translation are passed,
        # we add the translations together rather than picking one.
        matrix[0:3, 3] += kwargs['translation']
    return matrix
