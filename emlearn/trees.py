
"""
Tree-based models
=========================
"""

import os.path
import os

import numpy

from . import common, cgen

SUPPORTED_ESTIMATORS=[
    'RandomForestClassifier',
    'ExtraTreesClassifier',
    'DecisionTreeClassifier',
    'RandomForestRegressor',
    'ExtraTreesRegressor',
    'DecisionTreeRegressor',
]

def quantize_probabilities(p, bits=8):
    assert bits <= 8
    assert bits >= 1
    steps = (2**bits)-1

    bins = numpy.arange(0, steps)
    digits = numpy.digitize(p, bins)
    out = digits.astype(numpy.uint8)    
    return out    


# Tree representation as 2 arrays
# array of decision nodes:
# DNODE: feature, value, left_child, right_child
# array of leaf nodes
# LEAF: 
def flatten_tree(tree, leaf='argmax', leaf_bits=8):
    decision_nodes = []
    leaf_nodes = []

    assert tree.node_count == len(tree.value)
    assert tree.value.shape[1] == 1 # number of outputs

    def add_leaf(idx):
        """
        Returns an updated index value to identify the leaf
        """
        value = tree.value[idx]

        if leaf == 'argmax':
            # majority voting
            val = numpy.argmax(value[0])
        elif leaf == 'value':
            # regression
            val = value[0][0]
        elif leaf == 'probabilities':
            val = quantize_probabilities(value[0], bits=leaf_bits)

        leaf_data = val
        leaf_idx = len(leaf_nodes)
        leaf_nodes.append(leaf_data)
        encoded = -leaf_idx-1
        assert encoded <= -1 # 0 means decision node. So first leaf is -1
        return encoded

    def reference_node(idx):
        """
        Returns updated index value to identify decision node
        """
        
        n_leaves = len(leaf_nodes)
        decision_node_idx = idx - n_leaves
        #print('REF NODE', idx, decision_node_idx)
        assert decision_node_idx >= 0
        return decision_node_idx


    def process_child(idx): 
        is_leaf = tree.children_left[idx] == -1 and tree.children_right[idx] == -1
        if is_leaf:
            return add_leaf(idx)
        else:
            return idx # will be corrected later


    decision_node_mapping = {}
    leaves_seen = 0
    zipped = zip(tree.children_left, tree.children_right, tree.feature, tree.threshold, tree.value)
    for node_no, (left, right, feature, th, value) in enumerate(zipped):
        if left == -1 and right == -1:
            # is a leaf. Is handled via its parent
            leaves_seen += 1
            continue

        else:
            left = process_child(left)
            right = process_child(right)

            node = [ feature, th, left, right ]
            out_idx = len(decision_nodes)
            decision_nodes.append(node)
            decision_node_mapping[node_no] = out_idx
       
    # Update child decision node references to reflect smaller output nodes array
    for node in decision_nodes:
        if node[2] >= 0:
            node[2] = decision_node_mapping[node[2]]
        if node[3] >= 0:
            node[3] = decision_node_mapping[node[3]]


    total_nodes = len(decision_nodes) + len(leaf_nodes)
    assert total_nodes == tree.node_count, (total_nodes, tree.node_count)

    #print_tree((decision_nodes, leaf_nodes))

    assert_node_references_valid(decision_nodes, leaf_nodes, roots=[0])
    t = decision_nodes, leaf_nodes
    return t

def print_tree(tree):
    nodes, leaves = tree

    for i, n in enumerate(nodes):
        print('NODE', i, n)

    for i, n in enumerate(leaves):
        print('LEAF', i, n)

def print_forest(forest):
    nodes, roots, leaves = forest

    for i, n in enumerate(nodes):
        print('NODE', i, n)

    for i, n in enumerate(leaves):
        print('LEAF', i, n)

    for i, r in enumerate(roots):
        print('ROOT', i, r)

def assert_node_references_valid(nodes, leaves, roots):

    # INVARIANT. References to nodes in decision nodes are to valid nodes
    # TODO: check
    left_children = set([ n[2] for n in nodes if n[2] >= 0 ])
    right_children = set([ n[3] for n in nodes if n[3] >= 0 ])
    node_idxs = set(range(0, len(nodes)))

    invalid_children_left = left_children - node_idxs
    assert invalid_children_left == set(), invalid_children_left

    invalid_children_right = right_children - node_idxs
    assert invalid_children_right == set(), invalid_children_right

    extranous_nodes = node_idxs - (left_children | right_children | set(roots))
    assert extranous_nodes == set(), extranous_nodes

    # INVARIANT. References to leaves are to valid leaves
    left_leaves = set([ (-n[2])-1 for n in nodes if n[2] < 0 ])
    right_leaves = set([ (-n[3])-1 for n in nodes if n[3] < 0 ])
    leaf_idxs = set(range(0, len(leaves)))
    invalid_leaves_left = (left_leaves - leaf_idxs)
    assert invalid_leaves_left == set(), invalid_leaves_left

    invalid_leaves_right = (right_leaves - leaf_idxs)
    assert invalid_leaves_left == set(), invalid_leaves_left

    extranous_leaves = (leaf_idxs - (left_leaves | right_leaves))
    assert extranous_leaves == set(), extranous_leaves


def assert_forest_valid(forest):
    nodes, roots, leaves = forest

    assert_node_references_valid(nodes, leaves, roots)


def flatten_forest(trees, leaf='argmax'):
    tree_roots = []
    decision_nodes_offset = 0
    leaf_nodes_offset = 0
    forest_nodes = []
    forest_leaves = []

    for tree in trees: 
        decision_nodes, leaf_nodes = flatten_tree(tree, leaf=leaf)

        # Offset the nodes in tree, so they can be stored in one array 
        root = 0 + decision_nodes_offset
        for node in decision_nodes:
            if node[2] >= 0:
                node[2] += decision_nodes_offset
            else:
                node[2] -= leaf_nodes_offset
            if node[3] >= 0:
                node[3] += decision_nodes_offset
            else:
                node[3] -= leaf_nodes_offset
        decision_nodes_offset += len(decision_nodes)
        leaf_nodes_offset += len(leaf_nodes)

        tree_roots.append(root)
        forest_nodes += decision_nodes
        forest_leaves += leaf_nodes

        #print('offsets', decision_nodes_offset, leaf_nodes_offset)

        #print_forest((forest_nodes, tree_roots, forest_leaves))    
        assert_forest_valid((forest_nodes, tree_roots, forest_leaves))


    f = forest_nodes, tree_roots, forest_leaves
    #print_forest(f)    
    assert_forest_valid(f)
    return f


def remove_duplicate_leaves(forest):
    nodes, roots, leaves = forest

    # Determine de-duplicated leaves
    unique_leaves = []
    #unique_idx = []
    remap_leaves = {}
    for old_idx, node in enumerate(leaves):
        old_encoded = -old_idx-1
        found = unique_leaves.index(node) if node in unique_leaves else None
        if found is None:
            new_idx = len(unique_leaves)
            unique_leaves.append(node)
            #unique_idx.append(old_encoded)
        else:
            new_idx = found # unique_idx[found]
            #encoded = -new_idx-1

        new_encoded = -new_idx-1
        remap_leaves[old_encoded] = new_encoded


    wasted_ratio = (len(leaves) - len(unique_leaves)) / len(nodes)
    
    # Update decision nodes to point to new leaves
    for n in nodes:
        n[2] = remap_leaves.get(n[2], n[2])
        n[3] = remap_leaves.get(n[3], n[3])

    f = nodes, roots, unique_leaves
    #print_forest(f)
    assert_forest_valid(f)
    return f

def traverse_dfs(nodes, idx, visitor):
    if idx < 0:
        # this is a leaf
        return None
    visitor(idx)
    traverse_dfs(nodes, nodes[idx][2], visitor)
    traverse_dfs(nodes, nodes[idx][3], visitor)

def dot_node(name, **opts):
    return '{name} [label={label}];'.format(name=name, label=opts['label'])
def dot_edge(src, tgt, **opts):
    return '{src} -> {tgt} [taillabel={label}, labelfontsize={f}];'.format(src=src,tgt=tgt,label=opts['label'], f=opts['labelfontsize'])
def dot_cluster(name, nodes, indent='  '):
    name = 'cluster_' + name
    n = ('\n'+indent).join(nodes)
    return 'subgraph {name} {{\n  {nodes}\n}}'.format(name=name, nodes=n)

def forest_to_dot(forest, name='trees', indent="  "):
    nodes, roots, leaf_nodes = forest

    trees = [ [] for r in roots ]
    for tree_idx, root in enumerate(roots):
        collect = []
        traverse_dfs(nodes, root, lambda i: collect.append(i))
        trees[tree_idx] = set(collect).difference(leaf_nodes)

    edges = []
    leaves = []
    clusters = []

    # group trees using cluster
    for tree_idx, trees in enumerate(trees):
        decisions = []
        for idx in trees:
            node = nodes[idx]
            n = dot_node(idx, label='"{}: feature[{}] < {}"'.format(idx, node[0], node[1]))
            left = dot_edge(idx, node[2], label='"  1"', labelfontsize=8)
            right = dot_edge(idx, node[3], label='"  0"', labelfontsize=8)
            decisions += [ n ]
            edges += [ left, right]

        clusters.append(dot_cluster('_tree_{}'.format(tree_idx), decisions, indent=2*indent))

    # leaves shared between trees
    for idx, node in enumerate(leaf_nodes):
        value = str(node)
        leaves += [ dot_node(idx, label='"{}"'.format(value)) ]

    dot_items = clusters + edges + leaves

    graph_options = {
        #'rankdir': 'LR',
        #'ranksep': 0.07,
    }

    variables = {
        'name': name,
        'options': ('\n'+indent).join('{}={};'.format(k,v) for k,v in graph_options.items()),
        'items': ('\n'+indent).join(dot_items),
    }
    dot = """digraph {name} {{
      // Graph options
      {options}

      // Nodes/edges
      {items}
    }}""".format(**variables)

    return dot


def generate_c_nodes(flat, name, dtype='float'):
    child_value_max = 2**15
    child_value_min = -2**15

    def assert_valid_child(value):
        assert value >= child_value_min, value
        assert value <= child_value_max, value

    def make_node(index, node):
        feature, value, left_index, right_index = node

        # XXX: consider using relative jumps?
        left = left_index
        right = right_index
    
        assert_valid_child(left)
        assert_valid_child(right)

        value = cgen.constant(value, dtype=dtype)

        return "{{ {}, {}, {}, {} }}".format(feature, value, left, right)

    nodes_structs = ',\n  '.join(make_node(i, n) for i, n in enumerate(flat))
    nodes_name = name
    nodes_length = len(flat)
    nodes = "EmlTreesNode {nodes_name}[{nodes_length}] = {{\n  {nodes_structs} \n}};".format(**locals());

    out = nodes

    return out

def leaves_to_bytelist(leaves, leaf_bits):
    import math

    if leaf_bits == 0:
        return leaves

    elif leaf_bits == 32:
        arr = numpy.array(leaves).astype(numpy.float32)
        out = list(arr.tobytes())

        leaf_bytes = math.ceil(leaf_bits/8)
        expect_bytes = leaf_bytes*len(leaves)
        assert len(out) == expect_bytes, (len(out), expect_bytes) 
        return out
    else:
        # FIxME: support class proportions, with up to 8 bits
        raise ValueError('Only 0 or 32 supported for leaf_bits')

def generate_c_inlined(forest, name, n_features, n_classes=0, leaf_bits=0, dtype='float', classifier=True):
    nodes, roots, leaves = forest

    cgen.assert_valid_identifier(name)
    #assert leaf_bits == 0, 'class proportions not supported for inline yet'

    tree_names = [ name + '_tree_{}'.format(i) for i,_ in enumerate(roots) ]

    ctype = dtype
    leaf_dtype = 'int'
    if not classifier:
        leaf_dtype = 'float'
    indent = 2

    def c_leaf(data, depth):
        value = cgen.constant(data, dtype=leaf_dtype)
        return (depth*indent * ' ') + "return {};".format(value)
    def c_internal(n, depth):
        f = """{indent}if (features[{feature}] < {value}) {{
        {left}
        {indent}}} else {{
        {right}
        {indent}}}""".format(**{
            'feature': cgen.constant(n[0], dtype='int'),
            'value': cgen.constant(n[1], dtype=dtype),
            'left': c_node(n[2], depth+1),
            'right': c_node(n[3], depth+1),
            'indent': depth*indent*' ',
        })
        return f
    def c_node(idx, depth):
        if idx < 0:
            leaf_idx = -idx-1
            return c_leaf(leaves[leaf_idx], depth+1)
        else:
            return c_internal(nodes[idx], depth+1)


    def tree_func(name, root, return_type='int32_t'):
        return """static inline int32_t {function_name}(const {ctype} *features, int32_t features_length) {{
        {code}
        }}
        """.format(**{
            'function_name': name,
            'code': c_node(root, 0),
            'ctype': ctype,
            'return_type': return_type 
        })

    def tree_vote_classifier(name):
        return '_class = {}(features, features_length); votes[_class] += 1;'.format(name)

    def tree_vote_regressor(name):
        return 'avg += {}(features, features_length); '.format(name)

    forest_regressor_func = """float {function_name}(const {ctype} *features, int32_t features_length) {{

        float avg = 0;

        {tree_predictions}
        
        return avg/{n_trees};
    }}
    """.format(**{
      'function_name': name+"_predict",
      'n_classes': n_classes,
      'n_trees': len(roots),
      'tree_predictions': '\n    '.join([ tree_vote_regressor(n) for n in tree_names ]),
      'ctype': ctype,
    })

    forest_classifier_func = """int32_t {function_name}(const {ctype} *features, int32_t features_length) {{

        int32_t votes[{n_classes}] = {{0,}};
        int32_t _class = -1;

        {tree_predictions}
    
        int32_t most_voted_class = -1;
        int32_t most_voted_votes = 0;
        for (int32_t i=0; i<{n_classes}; i++) {{

            if (votes[i] > most_voted_votes) {{
                most_voted_class = i;
                most_voted_votes = votes[i];
            }}
        }}
        return most_voted_class;
    }}
    """.format(**{
      'function_name': name+"_predict",
      'n_classes': n_classes,
      'tree_predictions': '\n    '.join([ tree_vote_classifier(n) for n in tree_names ]),
      'ctype': ctype,
    })

    return_type = 'int32_t'
    forest_func = forest_classifier_func
    
    if not classifier:
        return_type = 'float'
        forest_func = forest_regressor_func

    tree_funcs = [tree_func(n, r, return_type=return_type) for n,r in zip(tree_names, roots)]

    return '\n\n'.join(tree_funcs + [forest_func])


def generate_c_loadable(forest, name, n_features, dtype='float', classifier=True, n_classes=0, leaf_bits=0):
    nodes, roots, leaves = forest

    cgen.assert_valid_identifier(name)

    nodes_name = name+'_nodes'
    nodes_length = len(nodes)
    nodes_c = generate_c_nodes(nodes, nodes_name, dtype=dtype)

    tree_roots_length = len(roots)
    tree_roots_name = name+'_tree_roots';
    tree_roots_values = ', '.join(str(t) for t in roots)
    tree_roots = 'int32_t {tree_roots_name}[{tree_roots_length}] = {{ {tree_roots_values} }};'.format(**locals())

    leaves_array = leaves_to_bytelist(leaves, leaf_bits=leaf_bits)
    leaves_length = len(leaves_array)
    leaves_name = name+'_leaves';
    leaves = cgen.array_declare(leaves_name, leaves_length,
            modifiers='static const', dtype='uint8_t', values=leaves_array)

    tree_leaf_bits = leaf_bits

    forest_struct = """EmlTrees {name} = {{
        {nodes_length},
        {nodes_name},	  
        {tree_roots_length},
        {tree_roots_name},
        {leaves_length},
        {leaves_name},
        {tree_leaf_bits},
        {n_features},
        {n_classes},
    }};""".format(**locals())

    head = """
    // !!! This file is generated using emlearn !!!

    #include <eml_trees.h>
    """

    code = '\n\n'.join([head, nodes_c, tree_roots, leaves, forest_struct])
    return code


class Wrapper:
    def __init__(self, estimator, classifier, dtype='float', leaf_bits=None):

        self.dtype = dtype

        kind = type(estimator).__name__
        leaf = 'argmax'
        self.is_classifier = True
        out_dtype = "int"
        if 'Regressor' in kind:
            leaf = 'value'
            self.is_classifier = False
            out_dtype = "float"

        if leaf_bits is None:
            if self.is_classifier:
                leaf_bits = 0
            else:
                leaf_bits = 32
        self.leaf_bits = leaf_bits

        if hasattr(estimator, 'estimators_'):
            estimators = [ e for e in estimator.estimators_]
        else:
            estimators = [ estimator ]

        trees = [ e.tree_ for e in estimators ]

        self.forest_ = flatten_forest(trees, leaf=leaf)
        self.forest_ = remove_duplicate_leaves(self.forest_)


        self.n_features = estimators[0].n_features_in_
        self.n_classes = 0
        if self.is_classifier:
            self.n_classes = estimators[0].n_classes_

        n_nodes = len(self.forest_[0])
        max_nodes = 2**15 # limited by int16_t for children in EmlTreeNode structure
        if n_nodes > max_nodes:
            raise ValueError(f"Model has {n_nodes} nodes. Max supported is {max_nodes} nodes.")


        if classifier == 'pymodule':
            # FIXME: use Nodes,Roots directly, as Numpy Array
            import eml_trees # import when required
            nodes, roots, leaves = self.forest_
            node_data = []
            for node in nodes:
                assert len(node) == 4
                node_data += node
            assert len(node_data) % 4 == 0

            assert type(roots) == list
            leaf_bytes = leaves_to_bytelist(leaves, leaf_bits=self.leaf_bits)
            self.classifier_ = eml_trees.Classifier(node_data, roots, leaf_bytes,
                self.leaf_bits, self.n_classes, self.n_features)

        elif classifier == 'loadable':
            name = 'mytree'
            proba_func = 'eml_trees_predict_proba(&{}, values, length, outputs, N_CLASSES)'\
                .format(name, self.n_classes)

            if self.is_classifier:
                func = 'eml_trees_predict(&{}, values, length)'.format(name)
            else:
                func = 'eml_trees_regress1(&{}, values, length)'.format(name)
            code = self.save(name=name)
            self.classifier_ = common.CompiledClassifier(code, name=name,
                call=func, proba_call=proba_func, out_dtype=out_dtype, n_classes=self.n_classes)
        elif classifier == 'inline':
            name = 'myinlinetree'
            # TODO: actually implement inline predict_proba, instead of just using loadable
            proba_func = 'eml_trees_predict_proba(&{}, values, length, outputs, N_CLASSES)'\
                .format(name, self.n_classes)
            func = '{}_predict(values, length)'.format(name)
            code = self.save(name=name)
            self.classifier_ = common.CompiledClassifier(code, name=name,
                call=func, proba_call=proba_func, out_dtype=out_dtype, n_classes=self.n_classes)
        else:
            raise ValueError("Unsupported classifier method '{}'".format(classifier))

    def predict(self, X):
        if self.is_classifier:
            predictions = self.classifier_.predict(X)
        else:
            predictions = self.classifier_.regress(X)            

        return predictions

    def predict_proba(self, X):
        if not self.is_classifier:
            raise ValueError(f"Cannot call predict_proba on a Regressor")
        
        probabilities = self.classifier_.predict_proba(X)
        return probabilities

    def save(self, name=None, file=None, format='c', inference=['inline', 'loadable']):
        if name is None:
            if file is None:
                raise ValueError('Either name or file must be provided')
            else:
                name = os.path.splitext(os.path.basename(file))[0]

        if format == 'c':
            code = ""
            generate_args = dict(forest=self.forest_,
                name=name,
                dtype=self.dtype,
                classifier=self.is_classifier,
                leaf_bits=self.leaf_bits,
                n_classes=self.n_classes,
                n_features=self.n_features,
            )
            if 'loadable' in inference:
                code += '\n\n' + generate_c_loadable(**generate_args)
            if 'inline' in inference:
                code += '\n\n' + generate_c_inlined(**generate_args)
            if not code:
                raise ValueError("No code generated. Check that 'inference' specifies valid strategies")

        elif format == 'csv':
            nodes, roots = self.forest_
            nodes = nodes.copy()
            lines = []
            for r in roots:
                lines.append(f'r,{r}')
            for n in nodes:
                lines.append(f'n,{n[0]},{n[1]},{n[2]},{n[3]}')
            code = '\r\n'.join(lines) 
        else:
            raise ValueError(f"Unsupported format: {format}")

        if file:
            with open(file, 'w') as f:
                f.write(code)

        return code

    def to_dot(self, **kwargs):
        return forest_to_dot(self.forest_, **kwargs)


