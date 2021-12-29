
import os.path
import os

import numpy

from . import common, cgen

# Ref 
"""
References

https://github.com/scikit-learn/scikit-learn/blob/95119c13af77c76e150b753485c662b7c52a41a2/sklearn/mixture/_gaussian_mixture.py#L380

https://github.com/scikit-learn/scikit-learn/blob/95119c13af77c76e150b753485c662b7c52a41a2/sklearn/mixture/_base.py

_estimate_log_gaussian_prob

log probability is used
implementation depends on covariance type. Can be known at fit time

Looks like log_det can be known at fit time
one constant per component

Stores
means_ means of each component
weights_ weights of each component

precisions_cholesky_
which is a Cholesky decomposition of the precision, the inverse of the covariance matrix

for full. components, features, features
for spherical. components
for diag. components, features
for tied, features, features 

BaysianGaussianMixture
https://github.com/scikit-learn/scikit-learn/blob/95119c13af77c76e150b753485c662b7c52a41a2/sklearn/mixture/_bayesian_mixture.py

Seem to reuse _estimate_log_gaussian_prob from GMM
Has an additional term log_lambda
does not seem to depend on X?


https://www.vlfeat.org/api/gmm.html
implements only diagonal covariance matrix

https://github.com/vlfeat/vlfeat/blob/master/vl/gmm.c#L712

"""

from sklearn.mixture._gaussian_mixture import _compute_log_det_cholesky
from sklearn.utils.extmath import row_norms
np = numpy


def _estimate_log_gaussian_prob(X, means, precisions_chol, covariance_type):
    """Estimate the log Gaussian probability.
    Parameters
    ----------
    X : array-like of shape (n_samples, n_features)
    means : array-like of shape (n_components, n_features)
    precisions_chol : array-like
        Cholesky decompositions of the precision matrices.
        'full' : shape of (n_components, n_features, n_features)
        'tied' : shape of (n_features, n_features)
        'diag' : shape of (n_components, n_features)
        'spherical' : shape of (n_components,)
    covariance_type : {'full', 'tied', 'diag', 'spherical'}
    Returns
    -------
    log_prob : array, shape (n_samples, n_components)
    """
    n_samples, n_features = X.shape
    n_components, _ = means.shape
    # det(precision_chol) is half of det(precision)
    log_det = _compute_log_det_cholesky(
        precisions_chol, covariance_type, n_features)

    if covariance_type == 'full':
        log_prob = np.empty((n_samples, n_components))

        #print('mm', n_samples, n_features, n_components)

        for i, x in enumerate(X):
            #print('x', i, x)
            for k, (mu, prec_chol) in enumerate(zip(means, precisions_chol)):

                pp = 0.0
   
                for f in range(x.shape[0]):
                    dot_m = 0.0
                    dot_x = 0.0

                    for p in range(prec_chol.shape[0]):
                        dot_m += (mu[p] * prec_chol[p,f])
                        dot_x += (x[p] * prec_chol[p,f])

                #assert dot_x == np.dot(x, prec_chol), 
                #assert dot_m == np.dot(mu, prec_chol), 

                    print('dot_x', dot_x)
                    print('dot_m', dot_m)
                    y = (dot_x - dot_m)
                    pp += ( y * y ) 

                #print('k', k, '\n', mu, '\n', prec_chol)
                dot_x = np.dot(x, prec_chol)
                dot_m = np.dot(mu, prec_chol)
                y = dot_x - dot_m

                print('dot_x', dot_x)
                print('dot_m', dot_m)
                #print('y', y)

                p = np.sum(np.square(y), axis=0) # sum over features
            
                assert p == pp, (p, pp)

                #print("log_prob", i, k, p)

                
                log_prob[i, k] = p

    elif covariance_type == 'tied':
        log_prob = np.empty((n_samples, n_components))
        for k, mu in enumerate(means):
            y = np.dot(X, precisions_chol) - np.dot(mu, precisions_chol)
            log_prob[:, k] = np.sum(np.square(y), axis=1)

    elif covariance_type == 'diag':
        precisions = precisions_chol ** 2
        log_prob = (np.sum((means ** 2 * precisions), 1) -
                    2. * np.dot(X, (means * precisions).T) +
                    np.dot(X ** 2, precisions.T))

    elif covariance_type == 'spherical':
        precisions = precisions_chol ** 2
        log_prob = (np.sum(means ** 2, 1) * precisions -
                    2 * np.dot(X, means.T * precisions) +
                    np.outer(row_norms(X, squared=True), precisions))


    s = -.5 * (n_features * np.log(2 * np.pi) + log_prob) + log_det
    print('s', s, log_det)

    return s


def init_model():
    

    pass


def get_covariance_type(s):
    if s == 'diag':
        s = 'diagonal'
    return 'EmlCovariance' + s.title()


def generate_code(model, name='fss_mode'):

    means = model._means
    log_det = model._log_det
    covar_type = get_covariance_type(model._covariance_type)
    precisions = model._precisions_col
    log_weights = numpy.log(model._weights)

    print(means.shape)
    print(log_det.shape)

    n_components = means.shape[0]
    n_features = 3

    means_name = f'{name}_means'
    means_size = n_components * n_features
    means_arr = cgen.array_declare(means_name, size=means_size, values=means.flatten())

    log_dets_name = f'{name}_log_dets'
    log_dets_arr = cgen.array_declare(log_dets_name, values=log_det.flatten())

    precisions_name = f'{name}_precisions'
    precisions_arr = cgen.array_declare(precisions_name, values=precisions.flatten())

    log_weights_name = f'{name}_log_weights'
    log_weights_arr = cgen.array_declare(log_weights_name, values=log_weights.flatten())


    predict_func = f'''
        int32_t
        {name}_log_proba(const float values[], int32_t values_length, float *out)
        {{

            return eml_mixture_log_proba(&{name}_model,
                                values, values_length,
                                out);
        }}
    '''

    model_init = f'EmlMixtureModel {name}_model = ' + cgen.struct_init(
        n_components,
        n_features,
        covar_type,
        means_name,
        precisions_name,
        log_dets_name,
        log_weights_name,
    ) + ';\n'

    preamble = """
    // !! This file was generated using emlearn

    #include <eml_mixture.h>
    """

    out = '\n'.join([
        preamble,
        means_arr,
        precisions_arr,
        log_weights_arr,
        log_dets_arr,
        model_init,
        predict_func,
    ])

    return out


def predict(bin_path, X, verbose=1):
    import subprocess

    def predict_one(x):
        args = [ bin_path ]
        args += [ str(v) for v in x ]
        out = subprocess.check_output(args)
        if verbose > 0:
            print(f"run args={args} out={out} ")

        lines = out.decode('utf-8').split('\n')
        for line in lines:
            print('l', line)

        outs = lines[-1].split(',')
        values = [ float(s) for s in outs ]
        return values

    y = [ predict_one(x) for x in numpy.array(X) ]
    return numpy.array(y)

def build_executable(wrapper, name='gmm'):
    n_components, n_features = wrapper._means.shape

    model_code = generate_code(wrapper, name=name)

    includes = """
    #include <stdio.h> // printf
    #include <stdlib.h> // stdod
    """

    code = includes + model_code + f"""

    static float features[{n_features}] = {{0.0}};
    static float output[{n_components}] = {{0.0}};

    int
    main(int argc, const char *argv[])
    {{
        const int n_features = {n_features};
        const int n_components = {n_components};

        if (argc != 1+n_features) {{
            return -1;
        }}

        for (int i=1; i<argc; i++) {{
            features[i-1] = strtod(argv[i], NULL);
        }}

        const EmlError out = {name}_log_proba(features, n_features, output);
        if (out != EmlOk) {{
            return -out; // error
        }}

        for (int i=0; i<n_components; i++) {{
            printf("%.6f", output[i]);
            if (i != (n_components-1)) {{
                printf(",");
            }}
        }}
        return 0;
    }}
    """
    

    # Compile the xor.c example program
    out_dir = './examples'
    src_path = os.path.join(out_dir, 'gmm.c')

    with open(src_path, 'w') as f:
        f.write(code)

    include_dirs = [ common.get_include_dir() ]
    bin_path = common.compile_executable(src_path, out_dir, include_dirs=include_dirs)

    return bin_path

def convert_to_full(means, precisions_chol, covariance_type):

    n_components, n_features = means.shape

    out = None
    if covariance_type == 'full':
        # already full covariance
        assert len(precisions_chol.shape) == 3, precisions_chol.shape
        out = precisions_chol

    elif covariance_type == 'tied':
        # all components share the same general covariance matrix
        assert len(precisions_chol.shape) == 2, precisions_chol.shape
        assert precisions_chol.shape[0] == n_features
        assert precisions_chol.shape[1] == n_features

        out = [ precisions_chol for _ in range(n_components) ]
        out = numpy.stack(out)

    elif covariance_type == 'diag':
        # each component has its own diagonal covariance matrix
        assert len(precisions_chol.shape) == 2, precisions_chol.shape
        assert precisions_chol.shape[0] == n_components
        assert precisions_chol.shape[1] == n_features

        out = [ numpy.diag(a) for a in precisions_chol ]
        out = numpy.stack(out)

    elif covariance_type == 'spherical':
        # each component has its own single variance
        assert len(precisions_chol.shape) == 1, precisions_chol.shape
        assert precisions_chol.shape[0] == n_components
        # copy out across features, features

        out = [ v * numpy.eye(n_features, n_features) for v in precisions_chol ]
        out = numpy.stack(out)

    else:
        raise ValueError("Unknown covariance_type '{}'")


    assert len(out.shape) == 3, out.shape
    assert out.shape == (n_components, n_features, n_features), out.shape
    return out

class Wrapper:
    def __init__(self, estimator, classifier, dtype='float'):
        self.dtype = dtype


        n_components, n_features = estimator.means_.shape
        print("est shape", n_components, n_features)
        covariance_type = estimator.covariance_type
        precisions_chol = estimator.precisions_cholesky_

        # Convert all types to "full" covariance matrix
        # TODO: native aupport for tied/diag/spherical
        precisions_chol = convert_to_full(estimator.means_, precisions_chol, covariance_type)
        covariance_type = 'full'

        from sklearn.mixture._gaussian_mixture import _compute_log_det_cholesky

        log_det = _compute_log_det_cholesky(
            precisions_chol, covariance_type, n_features)

        #print("log_det", log_det.shape)
        #print("means", estimator.means_.shape)
        #print("prec", precisions_chol.shape)

        self._log_det = log_det
        self._means = estimator.means_.copy()
        self._covariance_type = covariance_type
        self._precisions_col = precisions_chol
        self._weights = estimator.weights_


    def predict_proba(self, X):
        #from sklearn.mixture._gaussian_mixture import _estimate_log_gaussian_prob

        py_predictions = _estimate_log_gaussian_prob(X, self._means,
                                    self._precisions_col, self._covariance_type)

        py_predictions += numpy.log(self._weights)

        bin_path = build_executable(self)
        c_predictions = predict(bin_path, X)

        # XXX: note, this is actually log probabilities
        return c_predictions

    def predict(self, X):
        probabilities = self.predict_proba(X)
        predictions = numpy.argmax(probabilities, axis=1)
        return predictions

    def score_samples(self, X):
        from scipy.special import logsumexp

        prob = self.predict_proba(X)
        return logsumexp(prob, axis=1)


    def save(self, name=None, file=None):
        if name is None:
            if file is None:
                raise ValueError('Either name or file must be provided')
            else:
                name = os.path.splitext(os.path.basename(file))[0]

        code = "" # Implement
        if file:
            with open(file, 'w') as f:
                f.write(code)

        raise NotImplementedError("TODO implement save()")
        return code

