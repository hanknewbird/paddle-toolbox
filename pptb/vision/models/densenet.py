# @author: pithygit

import math
import paddle
from paddle.fluid.param_attr import ParamAttr
import paddle.nn as nn
import paddle.nn.functional as F
from paddle.nn import Conv2D, Linear, Dropout
from paddle.nn import MaxPool2D, AvgPool2D, AdaptiveAvgPool2D, BatchNorm
from paddle.nn.initializer import Uniform

from paddle.utils.download import get_weights_path_from_url


model_urls = {
    "DenseNet121": (
        "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/DenseNet121_pretrained.pdparams",
        "db1b239ed80a905290fd8b01d3af08e4",
    ),
    "DenseNet161": (
        "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/DenseNet161_pretrained.pdparams",
        "62158869cb315098bd25ddbfd308a853",
    ),
    "DenseNet169": (
        "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/DenseNet169_pretrained.pdparams",
        "82cc7c635c3f19098c748850efb2d796",
    ),
    "DenseNet201": (
        "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/DenseNet201_pretrained.pdparams",
        "16ca29565a7712329cf9e36e02caaf58",
    ),
    "DenseNet264": (
        "https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/DenseNet264_pretrained.pdparams",
        "3270ce516b85370bba88cfdd9f60bff4",
    ),
}


class BNACConvLayer(nn.Layer):
    def __init__(self, num_channels, num_filters, filter_size, stride=1, pad=0, groups=1, act="relu", name=None):
        super(BNACConvLayer, self).__init__()

        self._batch_norm = BatchNorm(
            num_channels,
            act=act,
            param_attr=ParamAttr(name=name + "_bn_scale"),
            bias_attr=ParamAttr(name + "_bn_offset"),
            moving_mean_name=name + "_bn_mean",
            moving_variance_name=name + "_bn_variance",
        )

        self._conv = Conv2D(
            in_channels=num_channels,
            out_channels=num_filters,
            kernel_size=filter_size,
            stride=stride,
            padding=pad,
            groups=groups,
            weight_attr=ParamAttr(name=name + "_weights"),
            bias_attr=False,
        )

    def forward(self, input):
        y = self._batch_norm(input)
        y = self._conv(y)
        return y


class DenseLayer(nn.Layer):
    def __init__(self, num_channels, growth_rate, bn_size, dropout, name=None):
        super(DenseLayer, self).__init__()
        self.dropout = dropout

        self.bn_ac_func1 = BNACConvLayer(
            num_channels=num_channels,
            num_filters=bn_size * growth_rate,
            filter_size=1,
            pad=0,
            stride=1,
            name=name + "_x1",
        )

        self.bn_ac_func2 = BNACConvLayer(
            num_channels=bn_size * growth_rate,
            num_filters=growth_rate,
            filter_size=3,
            pad=1,
            stride=1,
            name=name + "_x2",
        )

        if dropout:
            self.dropout_func = Dropout(p=dropout, mode="downscale_in_infer")

    def forward(self, input):
        conv = self.bn_ac_func1(input)
        conv = self.bn_ac_func2(conv)
        if self.dropout:
            conv = self.dropout_func(conv)
        conv = paddle.concat([input, conv], axis=1)
        return conv


class DenseBlock(nn.Layer):
    def __init__(self, num_channels, num_layers, bn_size, growth_rate, dropout, name=None):
        super(DenseBlock, self).__init__()
        self.dropout = dropout

        self.dense_layer_func = []

        pre_channel = num_channels
        for layer in range(num_layers):
            self.dense_layer_func.append(
                self.add_sublayer(
                    "{}_{}".format(name, layer + 1),
                    DenseLayer(
                        num_channels=pre_channel,
                        growth_rate=growth_rate,
                        bn_size=bn_size,
                        dropout=dropout,
                        name=name + "_" + str(layer + 1),
                    ),
                )
            )
            pre_channel = pre_channel + growth_rate

    def forward(self, input):
        conv = input
        for func in self.dense_layer_func:
            conv = func(conv)
        return conv


class TransitionLayer(nn.Layer):
    def __init__(self, num_channels, num_output_features, name=None):
        super(TransitionLayer, self).__init__()

        self.conv_ac_func = BNACConvLayer(
            num_channels=num_channels, num_filters=num_output_features, filter_size=1, pad=0, stride=1, name=name
        )

        self.pool2d_avg = AvgPool2D(kernel_size=2, stride=2, padding=0)

    def forward(self, input):
        y = self.conv_ac_func(input)
        y = self.pool2d_avg(y)
        return y


class ConvBNLayer(nn.Layer):
    def __init__(self, num_channels, num_filters, filter_size, stride=1, pad=0, groups=1, act="relu", name=None):
        super(ConvBNLayer, self).__init__()

        self._conv = Conv2D(
            in_channels=num_channels,
            out_channels=num_filters,
            kernel_size=filter_size,
            stride=stride,
            padding=pad,
            groups=groups,
            weight_attr=ParamAttr(name=name + "_weights"),
            bias_attr=False,
        )
        self._batch_norm = BatchNorm(
            num_filters,
            act=act,
            param_attr=ParamAttr(name=name + "_bn_scale"),
            bias_attr=ParamAttr(name + "_bn_offset"),
            moving_mean_name=name + "_bn_mean",
            moving_variance_name=name + "_bn_variance",
        )

    def forward(self, input):
        y = self._conv(input)
        y = self._batch_norm(y)
        return y


class DenseNet(nn.Layer):
    def __init__(self, layers=60, bn_size=4, dropout=0, num_classes=1000):
        super(DenseNet, self).__init__()

        supported_layers = [121, 161, 169, 201, 264]
        assert layers in supported_layers, "supported layers are {} but input layer is {}".format(
            supported_layers, layers
        )
        densenet_spec = {
            121: (64, 32, [6, 12, 24, 16]),
            161: (96, 48, [6, 12, 36, 24]),
            169: (64, 32, [6, 12, 32, 32]),
            201: (64, 32, [6, 12, 48, 32]),
            264: (64, 32, [6, 12, 64, 48]),
        }
        num_init_features, growth_rate, block_config = densenet_spec[layers]

        self.conv1_func = ConvBNLayer(
            num_channels=3, num_filters=num_init_features, filter_size=7, stride=2, pad=3, act="relu", name="conv1"
        )

        self.pool2d_max = MaxPool2D(kernel_size=3, stride=2, padding=1)

        self.block_config = block_config

        self.dense_block_func_list = []
        self.transition_func_list = []
        pre_num_channels = num_init_features
        num_features = num_init_features
        for i, num_layers in enumerate(block_config):
            self.dense_block_func_list.append(
                self.add_sublayer(
                    "db_conv_{}".format(i + 2),
                    DenseBlock(
                        num_channels=pre_num_channels,
                        num_layers=num_layers,
                        bn_size=bn_size,
                        growth_rate=growth_rate,
                        dropout=dropout,
                        name="conv" + str(i + 2),
                    ),
                )
            )

            num_features = num_features + num_layers * growth_rate
            pre_num_channels = num_features

            if i != len(block_config) - 1:
                self.transition_func_list.append(
                    self.add_sublayer(
                        "tr_conv{}_blk".format(i + 2),
                        TransitionLayer(
                            num_channels=pre_num_channels,
                            num_output_features=num_features // 2,
                            name="conv" + str(i + 2) + "_blk",
                        ),
                    )
                )
                pre_num_channels = num_features // 2
                num_features = num_features // 2

        self.batch_norm = BatchNorm(
            num_features,
            act="relu",
            param_attr=ParamAttr(name="conv5_blk_bn_scale"),
            bias_attr=ParamAttr(name="conv5_blk_bn_offset"),
            moving_mean_name="conv5_blk_bn_mean",
            moving_variance_name="conv5_blk_bn_variance",
        )

        self.pool2d_avg = AdaptiveAvgPool2D(1)

        stdv = 1.0 / math.sqrt(num_features * 1.0)

        self.out = Linear(
            num_features,
            num_classes,
            weight_attr=ParamAttr(initializer=Uniform(-stdv, stdv), name="fc_weights"),
            bias_attr=ParamAttr(name="fc_offset"),
        )

    def forward(self, input):
        conv = self.conv1_func(input)
        conv = self.pool2d_max(conv)

        for i, num_layers in enumerate(self.block_config):
            conv = self.dense_block_func_list[i](conv)
            if i != len(self.block_config) - 1:
                conv = self.transition_func_list[i](conv)

        conv = self.batch_norm(conv)
        y = self.pool2d_avg(conv)
        y = paddle.flatten(y, start_axis=1, stop_axis=-1)
        y = self.out(y)
        return y


def _DenseNet(arch, layers, pretrained, **kwargs):
    model = DenseNet(layers, **kwargs)
    if pretrained:
        assert (
            arch in model_urls
        ), "{} model do not have a pretrained model now, you should set pretrained=False".format(arch)
        weight_path = get_weights_path_from_url(model_urls[arch][0], model_urls[arch][1])

        param = paddle.load(weight_path)
        model.set_dict(param)

    return model


def densenet121(pretrained=False, **kwargs):
    """DenseNet 121-layer model

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    Examples:
        .. code-block:: python
            from paddle.vision.models import DenseNet121
            # build model
            model = DenseNet121()
            # build model and load imagenet pretrained weight
            # model = DenseNet121(pretrained=True)
    """
    return _DenseNet("DenseNet121", 121, pretrained, **kwargs)


def densenet161(pretrained=False, **kwargs):
    """DenseNet161 161-layer model

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet

    Examples:
        .. code-block:: python
            from paddle.vision.models import DenseNet161
            # build model
            model = DenseNet161()
            # build model and load imagenet pretrained weight
            # model = DenseNet161(pretrained=True)
    """
    return _DenseNet("DenseNet161", 161, pretrained, **kwargs)


def densenet169(pretrained=False, **kwargs):
    """DenseNet 169-layer model

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    Examples:
        .. code-block:: python
            from paddle.vision.models import DenseNet169
            # build model
            model = DenseNet169()
            # build model and load imagenet pretrained weight
            # model = DenseNet169(pretrained=True)
    """
    return _DenseNet("DenseNet169", 169, pretrained, **kwargs)


def densenet201(pretrained=False, **kwargs):
    """DenseNet 201-layer model

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    Examples:
        .. code-block:: python
            from paddle.vision.models import DenseNet201
            # build model
            model = DenseNet201()
            # build model and load imagenet pretrained weight
            # model = DenseNet201(pretrained=True)
    """
    return _DenseNet("DenseNet201", 201, pretrained, **kwargs)


def densenet264(pretrained=False, **kwargs):
    """DenseNet 264-layer model

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    Examples:
        .. code-block:: python
            from paddle.vision.models import DenseNet264
            # build model
            model = DenseNet264()
            # build model and load imagenet pretrained weight
            # model = DenseNet264(pretrained=True)
    """
    return _DenseNet("DenseNet264", 264, pretrained, **kwargs)