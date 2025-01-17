from typing import Sequence

import numpy as np
import paddle
import paddle.nn as nn
import pytest

from pptb.tools import (
    MixingDataController,
    cutmix_criterion,
    cutmix_data,
    cutmix_metric,
    mixup_criterion,
    mixup_data,
    mixup_metric,
)
from pptb.vision.models import resnext50_32x4d


class FakeModel(nn.Layer):
    def __init__(self, input_shape=(3, 224, 224), output_shape=(1000,)):
        super().__init__()
        self.input_shape = input_shape
        self.output_shape = output_shape

        self.flatten = nn.Flatten()
        self.fc = nn.Linear(np.prod(input_shape), np.prod(output_shape))

    def forward(self, x):
        x = self.flatten(x)
        x = self.fc(x)
        x = paddle.reshape(x, shape=[x.shape[0], *self.output_shape])
        return x


@pytest.mark.parametrize("batch_size", [1, 10])
def test_mixup(batch_size: int):
    fake_inputs = paddle.to_tensor((np.random.random((batch_size, 3, 224, 224)) * 255).astype(np.float32))
    fake_labels = paddle.to_tensor((np.random.random((batch_size, 1)).astype(np.int64)))
    model = resnext50_32x4d()
    mixup_alpha = 0.2
    loss_function = paddle.nn.CrossEntropyLoss()

    X_batch_mixed, y_batch_a, y_batch_b, lam = mixup_data(fake_inputs, fake_labels, mixup_alpha)
    predicts = model(X_batch_mixed)
    loss = mixup_criterion(loss_function, predicts, y_batch_a, y_batch_b, lam)
    acc = mixup_metric(paddle.metric.accuracy, predicts, y_batch_a, y_batch_b, lam)

    assert X_batch_mixed.dtype == fake_inputs.dtype
    assert y_batch_a.dtype == y_batch_b.dtype == fake_labels.dtype
    assert X_batch_mixed.shape == fake_inputs.shape
    assert y_batch_a.shape == y_batch_b.shape == fake_labels.shape
    # TODO: 这里的测试还是有点问题
    # upper_bound = np.ceil(fake_inputs.numpy().max(axis=0))
    # lower_bound = np.floor(fake_inputs.numpy().min(axis=0))
    # assert (X_batch_mixed.numpy().max(axis=0) <= upper_bound).all()
    # assert (X_batch_mixed.numpy().min(axis=0) >= lower_bound).all()


@pytest.mark.parametrize(
    "batch_size, data_shape, mix_axes",
    [
        (1, [3, 224, 224], [2, 3]),
        (10, [3, 224, 224], [2, 3]),
        (10, [1000, 25], [1]),
        (10, [1000, 50, 100], [1, 2, 3]),
    ],
)
def test_cutmix(batch_size: int, data_shape: Sequence[int], mix_axes: Sequence[int]):
    num_classes = 1
    fake_inputs = paddle.to_tensor(np.array(np.random.random((batch_size, *data_shape)), dtype=np.float32))
    fake_labels = paddle.to_tensor(np.array(np.random.random((batch_size, num_classes)), dtype=np.int64))
    model = FakeModel(data_shape, (num_classes,))
    cutmix_alpha = 0.2
    loss_function = paddle.nn.CrossEntropyLoss()

    X_batch_mixed, y_batch_a, y_batch_b, lam = cutmix_data(fake_inputs, fake_labels, cutmix_alpha, axes=mix_axes)
    predicts = model(X_batch_mixed)
    loss = cutmix_criterion(loss_function, predicts, y_batch_a, y_batch_b, lam)
    acc = cutmix_metric(paddle.metric.accuracy, predicts, y_batch_a, y_batch_b, lam)

    assert X_batch_mixed.dtype == fake_inputs.dtype
    assert y_batch_a.dtype == y_batch_b.dtype == fake_labels.dtype
    assert X_batch_mixed.shape == fake_inputs.shape
    assert y_batch_a.shape == y_batch_b.shape == fake_labels.shape
    keep_ratio = (X_batch_mixed.numpy() == fake_inputs.numpy()).sum().astype(np.float32) / np.prod(fake_inputs.shape)
    assert keep_ratio >= lam


@pytest.mark.parametrize(
    "is_numpy, mixup_prob, cutmix_prob",
    [
        (True, 0, 0),
        (True, 1, 0),
        (True, 0, 1),
        (False, 0, 0),
        (False, 1, 0),
        (False, 0, 1),
    ],
)
def test_mixing_data_controller(is_numpy: bool, mixup_prob: float, cutmix_prob: float):
    mixing_data_controller = MixingDataController(
        mixup_prob=mixup_prob,
        cutmix_prob=cutmix_prob,
        mixup_alpha=0.2,
        cutmix_alpha=0.2,
        cutmix_axes=[2, 3],
        loss_function=paddle.nn.CrossEntropyLoss(),
        metric_function=paddle.metric.accuracy,
    )
    batch_size = 16
    data_shape = (3, 224, 224)
    num_classes = 1
    model = FakeModel(data_shape, (num_classes,))

    fake_inputs = np.array(np.random.random((batch_size, *data_shape)), dtype=np.float32)
    fake_labels = np.array(np.random.random((batch_size, num_classes)), dtype=np.int64)

    if not is_numpy:
        fake_inputs = paddle.to_tensor(fake_inputs)
        fake_labels = paddle.to_tensor(fake_labels)

    X_batch_mixed, y_batch_a, y_batch_b, lam = mixing_data_controller.mix(fake_inputs, fake_labels, is_numpy=is_numpy)

    if is_numpy:
        X_batch_mixed = paddle.to_tensor(X_batch_mixed)
        y_batch_a = paddle.to_tensor(y_batch_a)
        y_batch_b = paddle.to_tensor(y_batch_b)

    predicts = model(X_batch_mixed)
    loss = mixing_data_controller.loss(predicts, y_batch_a, y_batch_b, lam)
    acc = mixing_data_controller.metric(predicts, y_batch_a, y_batch_b, lam)

    if is_numpy:
        fake_inputs = paddle.to_tensor(fake_inputs)
        fake_labels = paddle.to_tensor(fake_labels)

    assert X_batch_mixed.dtype == fake_inputs.dtype
    assert y_batch_a.dtype == y_batch_b.dtype == fake_labels.dtype
    assert X_batch_mixed.shape == fake_inputs.shape
    assert y_batch_a.shape == y_batch_b.shape == fake_labels.shape
