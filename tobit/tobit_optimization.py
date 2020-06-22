from data.assay_reader import Assay
import portion as p
import torch as t
from typing import List
import constants
import math
from scipy.stats import norm
import numpy as np
import matplotlib.pyplot as plt
from math import sqrt, pi, log
import tobit
from tobit.cdf_proximation import cdf_aproximation_4
from tobit.log_cdf_aproximation import Log1MinusCdfAproximation
from util.data import normalize, unnormalize, to_tensor, to_numpy

# this is the same as -sum(ln(gamma) + ln(pdf(gamma * y - delta)))
def pdf_negative_log_likelihood_reparametized(y, delta, gamma):
    return -t.sum(t.log(gamma) - ((gamma * y - delta) ** 2)/2)

def right_censored_cdf_negative_log_likelihood_reparametized(y, delta, gamma, log_of_1_minus_cdf_aproximation_model: t.nn.Module):
    return -t.sum(log_of_1_minus_cdf_aproximation_model(gamma * y - delta))

def read_normalized_tensors_from_assay_intervals(intervals: List[p.interval.Interval]):
    single_valued, right_censored, left_censored, all = [], [], [], []

    for interval in intervals:
        # If single valued interval
        if interval.lower == interval.upper:
            single_valued.append(interval.lower)
        elif interval.upper == p.inf:
            right_censored.append(interval.lower)
        elif interval.lower == 0:
            left_censored.append(interval.upper)
        else:
            raise Exception('Uncensored interval encountered', interval)

    all = np.array(single_valued + right_censored + left_censored)
    mean, std =  all.mean(), all.std() + 1e-10

    single_valued = t.tensor(single_valued, dtype=t.float, device=constants.DEVICE)
    right_censored = t.tensor(right_censored, dtype=t.float, device=constants.DEVICE)
    left_censored = t.tensor(left_censored, dtype=t.float, device=constants.DEVICE)

    single_valued = normalize(single_valued, mean, std)
    right_censored = normalize(right_censored, mean, std)
    left_censored = normalize(left_censored, mean, std)

    return single_valued, right_censored, left_censored, mean, std, len(all)

def load_log_1_minus_cdf_aproximation_model():
    model: t.nn.Module = Log1MinusCdfAproximation()
    model.load_state_dict(t.load(constants.LOG_1_MINUS_CDF_APROXIMATION_CHECKPOINT))
    model.eval()
    return model

def grad_log_cdf_by_delta_gamma(gamma: t.Tensor, delta: t.Tensor, y: t.Tensor):
    _gamma, _delta, _y = to_numpy(gamma), to_numpy(delta), to_numpy(y)
    x = -_gamma * _y + _delta
    pdf = norm.pdf(x)
    cdf = norm.cdf(x)
    d_delta = np.sum(pdf / cdf)
    d_gamma = -np.sum(_y * pdf / cdf)
    return d_delta, d_gamma

def tobit_mean_and_variance_reparametrization(intervals: List[p.interval.Interval], aproximation = False):
    if aproximation:
        log_1_minus_cdf_aprox_model = load_log_1_minus_cdf_aproximation_model()
    single_val, right_censored, left_censored, data_mean, data_std, N = read_normalized_tensors_from_assay_intervals(intervals)
    delta, gamma = to_tensor(0, grad = True), to_tensor(1, grad = True)
    optimizer = t.optim.SGD([delta, gamma], lr=1e-3)
    patience = 5
    for i in range(30_000):
        if i == 34:
            print('hi')

        prev_delta, prev_gamma = delta.clone(), gamma.clone()
        optimizer.zero_grad()

        # step 1 update based on pdf gradient (for uncensored data)
        log_likelihood_pdf = pdf_negative_log_likelihood_reparametized(single_val, delta, gamma)
        log_likelihood_pdf.backward()

        # step 2 compute the log(1 - cdf(x)) = log(cdf(-x)) gradient (for right censored data)
        if aproximation:
            log_likelihood_1_minus_cdf = right_censored_cdf_negative_log_likelihood_reparametized(
                right_censored, delta, gamma, log_1_minus_cdf_aprox_model)
            log_likelihood_1_minus_cdf.backward()
        else:
            d_delta, d_gamma = grad_log_cdf_by_delta_gamma(gamma, delta, right_censored)
            delta.grad -= to_tensor(d_delta)
            gamma.grad -= to_tensor(d_gamma)

        optimizer.step()
        early_stop = math.fabs(delta - prev_delta) + math.fabs(gamma - prev_gamma) < 1e-5
        if early_stop:
            patience -= 1
            if patience == 0:
                break
        else:
            patience = 5
        print(i, delta, gamma)
    mean, std = delta / gamma, 1 / gamma
    return unnormalize(mean, data_mean, data_std), std * data_std

def plot_gausian(mean, std):
    x = np.linspace(mean - 3 * std, mean + 3 * std, 1000)
    plt.plot(x, norm.pdf(x, mean, std))

if __name__ == '__main__':
    no_tobit = np.array([30, 50, 50])
    no_tobit_mean, no_tobit_std = norm.fit(no_tobit)

    ic50 = [ p.singleton(30), p.closed(50, p.inf), p.closed(50, p.inf)]
    mean, std = tobit_mean_and_variance_reparametrization(ic50, aproximation = False)

    print('No tobit mean', no_tobit_mean, 'std', no_tobit_std)
    plot_gausian(no_tobit_mean, no_tobit_std)
    print('Mean', mean, 'std', std)
    plot_gausian(to_numpy(mean), to_numpy(std))

    plt.show()

'''
    TODO : Bound variance at zero
    TODO: How to normalize the data
    TODO: Study the case of 50, (30, inf), (30, inf)
    TODO: Handle left censoring
    TODO: Initialize from data mean and std

    30, >50, >50
    2411
    43.333333333333336 9.428090415820632
    correct 58.0191 23.6987
    estimat 55.9838 22.8201

    30, >50, >50, >50, >50
    2066
    46.0 8.0
    correct 73.3895 29.4739
    estimat 76.3181 30.4512
    
    50, >30, >30
    99999
    36.66 9.42
    correct 49.9330 1.1563
    estimat 49.9355 1.1351
    
    Remains unchanged, because there was nothing to learn
    >30, >30, >30
    99999
    30 0
    30 1.0000e-10

'''