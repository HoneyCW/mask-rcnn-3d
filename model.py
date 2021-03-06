# -*- coding: utf-8 -*-
"""
   File Name:     model.py
   Description:   整体的前向传播过程
   Author:        steven.yi
   Date:          2019/5/10
"""
from torch import nn
from layers.base_net import UNet
from layers.net_head import RpnHead, MrcnnHead
from layers.anchors import generate_anchors
from layers.proposals import Proposal
from layers.target import RpnTarget, MrcnnTarget
from layers.roialign import RoiAlign
from layers.losses import rpn_cls_loss, rpn_regress_loss, mrcnn_cls_loss, mrcnn_regress_loss, mrcnn_mask_loss
from config import cur_config as config


class LungNet(nn.Module):
    def __init__(self, phase="train"):
        """
        构造函数
        :param phase: "train" or "test"
        """
        super(LungNet, self).__init__()
        self.phase = phase
        self.base_net = UNet()
        self.rpn_head = RpnHead(config.NUM_ANCHORS)
        self.mrcnn_head = MrcnnHead(in_channel=64)
        self.anchors = generate_anchors(config.ANCHOR_SCALES,
                                        config.STRIDE,
                                        config.FEATURES_HEIGHT,
                                        config.FEATURES_WIDTH,
                                        config.FEATURES_DEPTH)
        max_output_num = config.POST_NMS_ROIS_TRAINING if phase == 'train' else config.POST_NMS_ROIS_INFERENCE
        self.proposal = Proposal(config.PRE_NMS_LIMIT, config.RPN_NMS_THRESHOLD, max_output_num)
        self.mrcnn_traget = MrcnnTarget(config.TRAIN_ROIS_PER_IMAGE)
        self.rpn_target = RpnTarget()
        self.roi_align = RoiAlign(config.POOL_SIZE_HEIGHT, config.POOL_SIZE_WIDTH, config.POOL_SIZE_DEPTH,
                                  config.IMAGE_SIZE)

    def forward(self, x, gt_boxes=None, gt_labels=None, gt_masks=None):
        """
        前向传播
        :param x: tensor, [Batch, Channel, H, W, D]
        :param gt_boxes: list of numpy [n,(y1,x1,z1,y2,x2,z2)]
        :param gt_labels: list of numpy [n]
        :param gt_masks: numpy [n,H, W, D]
        :return: dict
        """
        outputs = {}

        # ### 网络输入、处理过程、输出
        # 获取feature map
        feature_map = self.base_net(x)
        # 获取rpn的输出
        rpn_output = self.rpn_head(feature_map)
        predict_logits, predict_deltas = rpn_output[:, :, -1], rpn_output[:, :, :-1]

        # 获取proposals
        batch_proposals, batch_scores, batch_indices = self.proposal(self.anchors, predict_logits, predict_deltas)

        if self.phase == 'train':
            # 获取mrcnn target
            batch_rois, mrcnn_deltas, mrcnn_labels, mrcnn_masks, rois_indices = self.mrcnn_traget(batch_proposals,
                                                                                                  batch_indices,
                                                                                                  gt_boxes, gt_labels,
                                                                                                  gt_masks)
            # roi align
            rois = self.roi_align(feature_map, batch_rois, rois_indices)
            # 获取mrcnn head输出
            predict_mrcnn_cls, predict_mrcnn_deltas, predict_mask = self.mrcnn_head(rois)
            #   ### 以下计算loss
            # 获取anchors的真实类别和真实偏移量
            gt_anchors_tag, gt_anchors_deltas = self.rpn_target(self.anchors, gt_boxes, gt_labels)
            # 计算rpn阶段loss
            cls_loss_rpn = rpn_cls_loss(gt_anchors_tag, predict_logits)
            regr_loss_rpn = rpn_regress_loss(gt_anchors_deltas, predict_deltas, gt_anchors_tag)
            # 计算mrcnn阶段loss
            cls_loss_mrcnn = mrcnn_cls_loss(mrcnn_labels, predict_mrcnn_cls)
            regr_loss_mrcnn = mrcnn_regress_loss(mrcnn_deltas, predict_mrcnn_deltas, mrcnn_labels)
            mask_loss = mrcnn_mask_loss(mrcnn_masks, predict_mask, mrcnn_labels)

            # 总的Loss
            total_loss = cls_loss_rpn + regr_loss_rpn + cls_loss_mrcnn + regr_loss_mrcnn + mask_loss
            outputs['loss'] = total_loss

        else:  #
            # roi align
            rois = self.roi_align(feature_map, batch_proposals, batch_indices)
            # 获取mrcnn head输出
            predict_mrcnn_cls, predict_mrcnn_deltas, predict_mask = self.mrcnn_head(rois)
            # todo: 还原检测框和预测的mask

        return outputs


def main():
    net = LungNet(phase='test')

    from torchsummary import summary

    summary(net, (1, 32, 32, 32))
    import torch
    inputs = torch.randn(2, 1, 32, 32, 32)
    out = net(inputs)
    print("output:{}".format(out))


if __name__ == '__main__':
    main()
