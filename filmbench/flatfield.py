import cv2
import numpy as np

from image_lab.validation import Invalid

from .density import CHANNEL_WEIGHTS


MIN_MEAN = .02           # 空拍平均线性亮度下限，过低无法可靠校正
MAX_REPAIRED = .25       # 允许修复的异常像素占比上限
GAIN_FLOOR = .05         # 归一化增益下限，避免暗角除法过度放大噪声
SMOOTH_SIDE = 1024       # 增益场平滑后的最长边
OUTLIER_FLOOR = .06      # 局部离群判定阈值下限


def build_gain(flat, size, encoding):
    """从空拍扫描构建照明增益场，先就地修复灰尘与过曝等局部异常。

    缓慢的大尺度照明变化（暗角、渐变偏色）全部保留在增益场中，
    只有相对局部参考明显离群的小区域会被当作异常修复。
    """
    if flat.size != tuple(size):
        raise Invalid('空拍与扫描尺寸不一致，请保持相机不动用相同设置重拍')
    linear = flat.linear(encoding)
    sample = linear[::8, ::8]
    if float(np.mean(sample @ CHANNEL_WEIGHTS)) < MIN_MEAN:
        raise Invalid('空拍整体太暗，无法校正照明，请提高曝光重拍')
    mask = anomalies(flat.codes, flat.maximum, linear)
    repaired = int(mask.sum())
    if repaired > MAX_REPAIRED * mask.size:
        raise Invalid('空拍异常区域过多（超过四分之一），请清洁灯箱并调整曝光后重拍')
    gain = smooth_gain(repair(linear, mask))
    info = {'repaired': repaired, 'repaired_fraction': round(repaired / mask.size, 5),
            'gain_min': round(float(gain[::4, ::4].min()), 4),
            'gain_max': round(float(gain[::4, ::4].max()), 4)}
    return gain, info


def anomalies(codes, maximum, linear):
    """标记截断像素和相对局部参考明显离群的像素（灰尘、过曝块）。

    局部参考是大尺度高斯模糊，会跟随缓慢的照明渐变，因此横跨半个
    画格的缓慢偏色不会被误判；两轮检测让参考不被异常本身污染。
    """
    mask = np.any((codes == 0) | (codes == maximum), axis=2)
    sigma = max(8., min(linear.shape[:2]) / 120)
    filled = linear
    for _ in range(2):
        reference = cv2.GaussianBlur(filled, (0, 0), sigma)
        ratio = linear / np.maximum(reference, 1e-4) - 1
        mask = mask | outliers(ratio, mask)
        filled = np.where(mask[..., None], reference, linear)
    kernel = np.ones((3, 3), np.uint8)
    return cv2.dilate(mask.view(np.uint8), kernel, iterations=2).astype(bool)


def outliers(ratio, known):
    stride = 4
    valid = ~known[::stride, ::stride]
    flag = np.zeros(ratio.shape[:2], bool)
    for channel in range(3):
        values = ratio[::stride, ::stride, channel][valid]
        if not len(values):
            continue
        center = np.median(values)
        spread = 1.4826 * np.median(np.abs(values - center))
        limit = max(OUTLIER_FLOOR, 6 * spread)
        flag |= np.abs(ratio[..., channel] - center) > limit
    return flag


def repair(linear, mask):
    if not mask.any():
        return linear
    radius = max(4, round(min(linear.shape[:2]) / 240))
    target = np.ascontiguousarray(mask, np.uint8) * 255
    filled = np.empty_like(linear)
    for channel in range(3):
        # TELEA 修复在 16 位整型上稳定；线性值按 65535 往返对 8/16 位源均无损
        codes = np.rint(linear[..., channel] * 65535).astype(np.uint16)
        restored = cv2.inpaint(codes, target, radius, cv2.INPAINT_TELEA)
        filled[..., channel] = restored.astype(np.float32) / 65535
    return filled


def smooth_gain(filled):
    """把修复后的空拍平滑成归一化增益场：降到平滑分辨率再升回，

    去掉逐像素噪声，保留暗角与缓慢偏色等大尺度照明结构。
    """
    height, width = filled.shape[:2]
    scale = SMOOTH_SIDE / max(height, width)
    if scale < 1:
        small = cv2.resize(filled, (max(1, round(width * scale)), max(1, round(height * scale))),
                           interpolation=cv2.INTER_AREA)
        smooth = cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)
    else:
        smooth = cv2.GaussianBlur(filled, (0, 0), 1.2)
    level = np.percentile(smooth[::4, ::4].reshape(-1, 3), 99.5, axis=0).astype(np.float32)
    gain = smooth / np.maximum(level, 1e-4)
    np.clip(gain, GAIN_FLOOR, 1., out=gain)
    return np.ascontiguousarray(gain, dtype=np.float32)
