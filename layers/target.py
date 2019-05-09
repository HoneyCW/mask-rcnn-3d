# -*- coding: utf-8 -*-
"""
   File Name：     target.py
   Description :  rpn和rcnn 分类、回归、分割目标
   Author :       mick.yi
   Date：          2019/5/8
"""
import torch
from torch import nn
import numpy as np
from utils.np_utils import iou_3d, regress_target_3d
from utils import torch_utils


class RpnTarget(nn.Module):
    """
    rpn target 接收numpy类型的anchors和gt 的list列表
    """

    def __init__(self, train_anchors_per_image=800, positive_iou_threshold=0.5,
                 negative_iou_threshold=0.02, train_positive_anchors=2):
        self.train_anchors_per_image = train_anchors_per_image
        self.positive_iou_threshold = positive_iou_threshold
        self.negative_iou_threshold = negative_iou_threshold
        self.train_positive_anchors = train_positive_anchors
        super(RpnTarget, self).__init__()

    def forward(self, anchors, gt_boxes, gt_labels):
        """

        :param anchors: numpy 数组[anchors_num,(y1,x1,z1,y2,x2,z2)]
        :param gt_boxes: list of numpy [n,(y1,x1,z1,y2,x2,z2)]
        :param gt_labels: list of numpy [n]
        :return: anchors_tag: torch tensor [batch,anchors_num]  1-正样本，-1-负样本，0-不参与训练
        :return: deltas: torch tensor  [batch,anchors_num,(dy,dx,dz,dh,dw,dd)]
        """
        batch_anchors_tag = []
        batch_deltas = []
        # 逐个样本处理
        for boxes, labels in zip(gt_boxes, gt_labels):
            boxes = np.expand_dims(boxes, axis=0)
            iou = iou_3d(boxes, anchors)  # [gt_num,anchors_num]
            anchors_iou_max = np.max(iou, axis=0)  # [anchors_num]
            anchors_tag = np.zeros_like(anchors[:, 0], np.int)  # [anchors_num]
            # 正负样本
            pos_indices = np.where(anchors_iou_max >= self.positive_iou_threshold)[0]
            neg_indices = np.where(anchors_iou_max <= self.negative_iou_threshold)[0]
            # 正样本采样
            pos_num = min(pos_indices.shape[0], self.train_positive_anchors)
            np.random.shuffle(pos_indices)
            pos_indices = pos_indices[:pos_num]
            anchors_tag[pos_indices] = 1
            # 负样本采样
            neg_num = min(neg_indices.shape[0], self.train_anchors_per_image - pos_num)
            np.random.shuffle(neg_indices)
            neg_indices = neg_indices[:neg_num]
            print(pos_indices, neg_indices)
            anchors_tag[neg_indices] = -1

            # 计算回归目标
            anchors_iou_argmax = np.argmax(iou, axis=0)  # [anchors_num]
            pos_gt_indices = anchors_iou_argmax[pos_indices]  # 正样本对应的gt索引号
            pos_gt_boxes = boxes[pos_gt_indices]
            pos_anchors = anchors[pos_indices]
            deltas = np.zeros_like(anchors)  # [anchors_num,(dy,dx,dz,dh,dw,dd)]
            deltas[pos_indices] = regress_target_3d(pos_anchors, pos_gt_boxes)

            # 转为tensor
            batch_anchors_tag.append(torch.from_numpy(anchors_tag).cuda())
            batch_deltas.append(torch.from_numpy(deltas).cuda())

        batch_anchors_tag = torch.stack(batch_anchors_tag, dim=0)
        batch_deltas = torch.stack(batch_deltas, dim=0)
        return batch_anchors_tag, batch_deltas


class MrcnnTarget(nn.Module):
    """
    计算mrcnn网络的分类、回归、分割 目标；还有一个作用就是过滤训练的proposals
    """

    def __init__(self, train_rois_per_image, positive_iou_threshold=0.5,
                 negative_iou_threshold=0.02, positive_ratio=0.1):
        super(MrcnnTarget, self).__init__()
        self.train_rois_per_image = train_rois_per_image
        self.positive_iou_threshold = positive_iou_threshold
        self.negative_iou_threshold = negative_iou_threshold

    def forward(self, proposals, batch_indices, gt_boxes, gt_labels):
        """

        :param proposals: tensor [proposals_num,(y1,x1,z1,y2,x2,z2)]
        :param batch_indices: proposal在原始的mini-batch中的索引号，tensor [proposals_num]
        :param gt_boxes: list of numpy [n,(y1,x1,z1,y2,x2,z2)]
        :param gt_labels: list of numpy [n]

        :return: rois: tensor [rois_num,(y1,x1,z1,y2,x2,z2)]
        :return: deltas: tensor [rois_num,(y1,x1,z1,y2,x2,z2)]
        :return: labels: tensor [rois_num,(y1,x1,z1,y2,x2,z2)]
        :return: rois_indices: tensor [rois_num] roi在原始的mini-batch中的索引号;roiAlign时用到
        :return: rois_tag: tensor [rois_num]  1-正样本，-1-负样本
        """
        batch_rois = []
        batch_deltas = []
        batch_labels = []
        batch_rois_indices = []
        batch_rois_tag = []
        # 逐个样本处理
        for i in range(len(gt_boxes)):
            # gt to gpu
            boxes = torch.from_numpy(gt_boxes[i]).cuda()
            labels = torch.from_numpy(gt_labels[i]).cuda()
            # 属于第i个样本的proposals
            roi_indices = (proposals == i).nonzero()[:, 0]  # 索引
            rois = torch.index_select(proposals, 0, roi_indices)
            # 计算iou
            iou = torch_utils.iou_3d(boxes, rois)  # [gt_num,roi_num]
            # 正样本
            roi_max, _ = torch.max(iou, 0, keepdim=True)  # 每个roi最大的iou值
            pos_indices = (roi_max == iou) * (iou >= self.positive_iou_threshold)  # 正样本索引
            pos_indices = pos_indices.nonzero()  # [n,(gt_idx,roi_idx)]
            # 负样本
            neg_indices = (roi_max == iou) * (iou < self.negative_iou_threshold)
            neg_indices = neg_indices.nonzero()

            # 采样
            pos_num = int(self.positive_ratio * self.train_rois_per_image)
            pos_num = min(pos_num, pos_indices.shape[0])
            pos_indices = torch_utils.shuffle_and_select(pos_indices, pos_num)

            neg_num = min(self.train_rois_per_image - pos_num, neg_indices.shape[0])
            neg_indices = torch_utils.shuffle_and_select(neg_indices, neg_num)
            # 合并indices
            indices = torch.cat([pos_indices, neg_indices], dim=0)
            roi_indices = indices[:, 1]
            gt_indices = indices[:, 0]
            rois = torch.index_select(rois, 0, roi_indices)
            boxes = torch.index_select(boxes, 0, gt_indices)
            labels = torch.index_select(labels, 0, gt_indices)
            labels[-neg_num:] = 0  # 负样本标签为0
            # 计算回归目标
            deltas = torch_utils.regress_target_3d(rois, boxes)

            # 生成roi_tag,新的batch_indices
            batch_rois_tag.append(torch.Tensor([1] * pos_num + [-1] * neg_num).cuda())
            batch_rois_indices.append(torch.Tensor([i] * (pos_num + neg_num)).cuda())

            # 添加rois,deltas,labels
            batch_rois.append(rois)
            batch_deltas.append(deltas)
            batch_labels.append(labels)

        # 在拼接到一块
        batch_rois = torch.cat(batch_rois, dim=0)
        batch_deltas = torch.cat(batch_deltas, dim=0)
        batch_labels = torch.cat(batch_labels, dim=0)
        batch_rois_tag = torch.cat(batch_rois_tag, dim=0)
        batch_rois_indices = torch.cat(batch_rois_indices, dim=0)

        return batch_rois, batch_deltas, batch_labels, batch_rois_tag, batch_rois_indices
