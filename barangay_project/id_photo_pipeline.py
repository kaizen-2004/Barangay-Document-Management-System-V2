from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

_REMBG_SESSION = None
_REMBG_MODEL_NAME = "u2net_human_seg"
_FALLBACK_MODEL_NAME = "isnet-general-use"

PIPELINE_PRESETS = {
    "id_photo": {
        "normalize": True,
        "denoise_strength": 10,
        "sharpen_amount": 0.5,
        "sharpen_radius": 1.0,
        "trimap_expansion": 12,
        "refine_boundary": True,
        "guided_filter_radius": 10,
        "guided_filter_eps": 1e-6,
        "complexity_threshold": 0.35,
        "decontaminate": True,
    },
    "portrait": {
        "normalize": True,
        "denoise_strength": 8,
        "sharpen_amount": 0.7,
        "sharpen_radius": 1.5,
        "trimap_expansion": 15,
        "refine_boundary": True,
        "guided_filter_radius": 15,
        "guided_filter_eps": 1e-6,
        "complexity_threshold": 0.30,
        "decontaminate": True,
    },
    "fast": {
        "normalize": False,
        "denoise_strength": 0,
        "sharpen_amount": 0.0,
        "sharpen_radius": 1.0,
        "trimap_expansion": 5,
        "refine_boundary": False,
        "guided_filter_radius": 5,
        "guided_filter_eps": 1e-5,
        "complexity_threshold": 0.45,
        "decontaminate": False,
    },
}


class IDPhotoPipeline:
    def __init__(self, preset="id_photo", **overrides):
        cfg = dict(PIPELINE_PRESETS.get(preset, PIPELINE_PRESETS["id_photo"]))
        cfg.update(overrides)
        self.normalize_enabled = cfg["normalize"]
        self.denoise_strength = cfg["denoise_strength"]
        self.sharpen_amount = cfg["sharpen_amount"]
        self.sharpen_radius = cfg["sharpen_radius"]
        self.trimap_expansion = cfg["trimap_expansion"]
        self.refine_boundary = cfg["refine_boundary"]
        self.guided_filter_radius = cfg["guided_filter_radius"]
        self.guided_filter_eps = cfg["guided_filter_eps"]
        self.complexity_threshold = cfg["complexity_threshold"]
        self.decontaminate = cfg["decontaminate"]
        self.skip_normalize = cfg.get("skip_normalize", False)
        self._session = None
        self._session_model = None

    def _get_rembg_session(self):
        global _REMBG_SESSION, _REMBG_MODEL_NAME, _FALLBACK_MODEL_NAME
        from rembg import new_session
        import os as _os

        model_name = _os.environ.get("ID_PHOTO_MODEL", "")
        if not model_name:
            try:
                from flask import current_app

                if current_app:
                    model_name = current_app.config.get("ID_PHOTO_MODEL", "")
            except Exception:
                current_app.logger.exception("Failed to get current_app in pipeline")
                pass
        if not model_name:
            model_name = _REMBG_MODEL_NAME

        if _REMBG_SESSION is None or self._session_model != model_name:
            try:
                _REMBG_SESSION = new_session(model_name)
                self._session_model = model_name
            except Exception:
                current_app.logger.exception("Failed to load BiRefNet model, trying fallback")
                if model_name != _FALLBACK_MODEL_NAME:
                    _REMBG_SESSION = new_session(_FALLBACK_MODEL_NAME)
                    self._session_model = _FALLBACK_MODEL_NAME
                else:
                    raise
        return _REMBG_SESSION

    def process(self, input_path: str) -> np.ndarray:
        image = self._load_image(input_path)

        h, w = image.shape[:2]
        max_dim = int(os.environ.get("BG_REMOVAL_MAX_DIMENSION", "320"))
        if max_dim > 0 and max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        metrics = self._assess_quality(image)

        if self.normalize_enabled and metrics["needs_any"] and not self.skip_normalize:
            image = self._normalize(image, metrics)
        elif self.skip_normalize and (metrics["needs_exposure"] or metrics["needs_wb"]):
            image = self._adjust_brightness(image, metrics)

        alpha = self._segment(image)

        trimap = self._generate_trimap(alpha)

        complexity = self._analyze_boundary_complexity(trimap)
        if self.refine_boundary and complexity > self.complexity_threshold:
            alpha = self._refine_boundary(image, alpha, trimap)
        else:
            alpha = self._refine_boundary_direct(alpha, trimap)

        alpha = self._optimize_alpha(alpha)

        result = self._remove_artifacts(image, alpha)
        return result

    def process_to_file(self, input_path: str, output_path: str) -> str:
        rgba = self.process(input_path)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgba, "RGBA").save(output_path, format="PNG")
        return output_path

    @staticmethod
    def _load_image(input_path: str) -> np.ndarray:
        with Image.open(input_path) as pil_img:
            pil_img = ImageOps.exif_transpose(pil_img)
            pil_img = pil_img.convert("RGB")
        return np.array(pil_img, dtype=np.uint8)

    @staticmethod
    def _assess_quality(image: np.ndarray) -> dict:
        lab = cv2.cvtColor(image, cv2.COLOR_RGB2LAB).astype(np.float32)

        h, w = lab.shape[:2]
        center_y = h // 4
        center_x = w // 4
        face_roi = lab[max(0, center_y):min(h, h - center_y),
                       max(0, center_x):min(w, w - center_x)]

        if face_roi.size == 0:
            face_roi = lab

        l_channel = face_roi[:, :, 0]
        a_channel = face_roi[:, :, 1]
        b_channel = face_roi[:, :, 2]

        l_mean = float(np.mean(l_channel))
        a_mean = float(np.mean(a_channel))
        b_mean = float(np.mean(b_channel))

        l_std = float(np.std(l_channel))
        l_var = cv2.Laplacian(lab[:, :, 0].astype(np.uint8), cv2.CV_64F).var()

        needs_exposure = l_mean < 70 or l_mean > 170
        needs_wb = abs(a_mean - 128) > 12 or abs(b_mean - 128) > 12
        needs_contrast = l_std < 28
        needs_sharpen = l_var < 80
        needs_denoise = False

        return {
            "l_mean": l_mean,
            "a_mean": a_mean,
            "b_mean": b_mean,
            "l_std": l_std,
            "l_var": l_var,
            "needs_exposure": needs_exposure,
            "needs_wb": needs_wb,
            "needs_contrast": needs_contrast,
            "needs_sharpen": needs_sharpen,
            "needs_denoise": needs_denoise,
            "needs_any": needs_exposure or needs_wb or needs_contrast or needs_sharpen,
        }

    def _normalize(self, image: np.ndarray, metrics: dict) -> np.ndarray:
        result = image.copy()
        lab = cv2.cvtColor(result, cv2.COLOR_RGB2LAB).astype(np.float32)

        if metrics["needs_exposure"]:
            l_channel = lab[:, :, 0]
            low, high = np.percentile(l_channel, (2, 98))
            if high - low > 20:
                l_scaled = (l_channel - low) * (255.0 / max(high - low, 1))
                lab[:, :, 0] = np.clip(l_scaled, 0, 255)
            elif metrics["l_mean"] < 70:
                lab[:, :, 0] = np.clip(l_channel * 1.3, 0, 255)
            elif metrics["l_mean"] > 170:
                lab[:, :, 0] = np.clip(l_channel * 0.7, 0, 255)

        if metrics["needs_wb"]:
            a_shift = 128.0 - metrics["a_mean"]
            b_shift = 128.0 - metrics["b_mean"]
            a_scale = min(abs(a_shift) / 64.0, 1.0) if abs(a_shift) > 0 else 0
            b_scale = min(abs(b_shift) / 64.0, 1.0) if abs(b_shift) > 0 else 0
            lab[:, :, 1] = np.clip(lab[:, :, 1] + a_shift * a_scale, 0, 255)
            lab[:, :, 2] = np.clip(lab[:, :, 2] + b_shift * b_scale, 0, 255)

        result = cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2RGB)

        if metrics["needs_contrast"]:
            lab2 = cv2.cvtColor(result, cv2.COLOR_RGB2LAB)
            l_channel = lab2[:, :, 0]
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            lab2[:, :, 0] = clahe.apply(l_channel)
            result = cv2.cvtColor(lab2, cv2.COLOR_LAB2RGB)

        if self.denoise_strength > 0:
            result = cv2.bilateralFilter(
                result,
                d=5,
                sigmaColor=self.denoise_strength,
                sigmaSpace=self.denoise_strength,
            )

        if self.sharpen_amount > 0:
            result = self._unsharp_mask(result, self.sharpen_amount, self.sharpen_radius)

        return result

    def _adjust_brightness(self, image: np.ndarray, metrics: dict) -> np.ndarray:
        result = image.copy()
        lab = cv2.cvtColor(result, cv2.COLOR_RGB2LAB).astype(np.float32)

        if metrics["needs_exposure"]:
            l_channel = lab[:, :, 0]
            low, high = np.percentile(l_channel, (2, 98))
            if high - low > 20:
                l_scaled = (l_channel - low) * (255.0 / max(high - low, 1))
                lab[:, :, 0] = np.clip(l_scaled, 0, 255)
            elif metrics["l_mean"] < 70:
                lab[:, :, 0] = np.clip(l_channel * 1.3, 0, 255)
            elif metrics["l_mean"] > 170:
                lab[:, :, 0] = np.clip(l_channel * 0.7, 0, 255)

        if metrics["needs_wb"]:
            a_shift = 128.0 - metrics["a_mean"]
            b_shift = 128.0 - metrics["b_mean"]
            a_scale = min(abs(a_shift) / 64.0, 1.0) if abs(a_shift) > 0 else 0
            b_scale = min(abs(b_shift) / 64.0, 1.0) if abs(b_shift) > 0 else 0
            lab[:, :, 1] = np.clip(lab[:, :, 1] + a_shift * a_scale, 0, 255)
            lab[:, :, 2] = np.clip(lab[:, :, 2] + b_shift * b_scale, 0, 255)

        result = cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2RGB)
        return result

    @staticmethod
    def _unsharp_mask(image: np.ndarray, amount: float, radius: float) -> np.ndarray:
        if amount <= 0:
            return image
        blurred = cv2.GaussianBlur(image, (0, 0), sigmaX=radius)
        sharpened = cv2.addWeighted(image, 1.0 + amount, blurred, -amount, 0)
        return np.clip(sharpened, 0, 255).astype(np.uint8)

    def _segment(self, image: np.ndarray) -> np.ndarray:
        from rembg import remove
        import os as _os

        h, w = image.shape[:2]
        max_dim = int(_os.environ.get("BG_REMOVAL_MAX_DIMENSION", "800"))
        scale = 1.0
        if max_dim > 0 and max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            small = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        else:
            small = image

        session = self._get_rembg_session()
        rgb_pil = Image.fromarray(small, "RGB")
        buf = BytesIO()
        rgb_pil.save(buf, format="PNG")
        input_bytes = buf.getvalue()

        output_bytes = remove(
            input_bytes,
            session=session,
            alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=0,
            post_process_mask=True,
        )

        with Image.open(BytesIO(output_bytes)) as result:
            rgba = ImageOps.exif_transpose(result).convert("RGBA")

        rgba_np = np.array(rgba, dtype=np.uint8)
        alpha = rgba_np[:, :, 3].astype(np.float32) / 255.0

        if scale < 1.0:
            alpha = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_LINEAR)

        return alpha

    def _generate_trimap(self, alpha: np.ndarray) -> np.ndarray:
        trimap = np.zeros_like(alpha, dtype=np.uint8)
        trimap[alpha >= 0.95] = 255
        trimap[alpha <= 0.05] = 0
        unknown_mask = (alpha > 0.05) & (alpha < 0.95)
        trimap[unknown_mask] = 128

        fg = (trimap == 255).astype(np.uint8)
        bg = (trimap == 0).astype(np.uint8)

        expansion = self.trimap_expansion
        if expansion > 0:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (expansion, expansion))
            fg_dilated = cv2.dilate(fg, kernel, iterations=1)
            bg_dilated = cv2.dilate(bg, kernel, iterations=1)
            transition = fg_dilated & bg_dilated
            trimap[transition > 0] = 128
            trimap[fg > 0] = 255
            trimap[bg > 0] = 0

        return trimap

    @staticmethod
    def _analyze_boundary_complexity(trimap: np.ndarray) -> float:
        unknown = (trimap == 128).astype(np.float32)
        unknown_ratio = float(np.sum(unknown)) / max(trimap.size, 1)

        if unknown_ratio < 0.001:
            return 0.0

        grad_x = cv2.Sobel(unknown, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(unknown, cv2.CV_32F, 0, 1, ksize=3)
        grad_mag = np.sqrt(grad_x**2 + grad_y**2)

        edge_mask = grad_mag > 0.1
        if not np.any(edge_mask):
            return 0.0

        complexity = float(np.mean(grad_mag[edge_mask]))

        return min(complexity + unknown_ratio * 2.0, 1.0)

    def _refine_boundary(self, image: np.ndarray, alpha: np.ndarray, trimap: np.ndarray) -> np.ndarray:
        return self._guided_filter_alpha(image, alpha, trimap)

    def _refine_boundary_direct(self, alpha: np.ndarray, trimap: np.ndarray) -> np.ndarray:
        unknown = (trimap == 128)
        result = alpha.copy()

        if np.any(unknown):
            dist = cv2.distanceTransform((alpha < 0.5).astype(np.uint8), cv2.DIST_L1, 3)
            dist_fg = cv2.distanceTransform((alpha >= 0.5).astype(np.uint8), cv2.DIST_L1, 3)
            total = dist + dist_fg
            valid = total > 0
            blend = np.where(valid, dist_fg / total, alpha)
            result[unknown] = blend[unknown]

        return np.clip(result, 0.0, 1.0)

    def _guided_filter_alpha(self, image: np.ndarray, alpha: np.ndarray, trimap: np.ndarray) -> np.ndarray:
        guide = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        radius = self.guided_filter_radius
        eps = self.guided_filter_eps

        kernel_size = (radius * 2 + 1, radius * 2 + 1)

        mean_i = cv2.boxFilter(guide, -1, kernel_size, normalize=True)
        mean_p = cv2.boxFilter(alpha, -1, kernel_size, normalize=True)
        mean_ip = cv2.boxFilter(guide * alpha, -1, kernel_size, normalize=True)
        cov_ip = mean_ip - mean_i * mean_p

        mean_ii = cv2.boxFilter(guide * guide, -1, kernel_size, normalize=True)
        var_i = mean_ii - mean_i * mean_i

        a = cov_ip / (var_i + eps)
        b = mean_p - a * mean_i

        mean_a = cv2.boxFilter(a, -1, kernel_size, normalize=True)
        mean_b = cv2.boxFilter(b, -1, kernel_size, normalize=True)

        refined = mean_a * guide + mean_b

        unknown_mask = (trimap == 128)
        result = alpha.copy()
        if np.any(unknown_mask):
            result[unknown_mask] = refined[unknown_mask]

        return np.clip(result, 0.0, 1.0)

    @staticmethod
    def _optimize_alpha(alpha: np.ndarray) -> np.ndarray:
        result = alpha.copy()

        binary = (alpha >= 0.5).astype(np.uint8)

        try:
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
            for label_id in range(1, num_labels):
                if stats[label_id, cv2.CC_STAT_AREA] < 50:
                    binary[labels == label_id] = 0

            inv_binary = 1 - binary
            num_labels_bg, labels_bg, stats_bg, _ = cv2.connectedComponentsWithStats(inv_binary, connectivity=8)
            for label_id in range(1, num_labels_bg):
                if stats_bg[label_id, cv2.CC_STAT_AREA] < 50:
                    binary[labels_bg == label_id] = 1
        except Exception:
            pass

        transition = (alpha > 0.1) & (alpha < 0.9)
        if np.any(transition):
            try:
                blurred = cv2.GaussianBlur(alpha, (3, 3), sigmaX=0.5)
                result[transition] = blurred[transition]
            except Exception:
                pass

        return np.clip(result, 0.0, 1.0)

    def _remove_artifacts(self, image: np.ndarray, alpha: np.ndarray) -> np.ndarray:
        image_f = image.astype(np.float32)
        alpha_u8 = (alpha * 255).astype(np.uint8)

        if self.decontaminate:
            transition = (alpha > 0.05) & (alpha < 0.95)
            if np.any(transition):
                try:
                    eroded_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
                    alpha_eroded = cv2.erode(alpha_u8, eroded_kernel, iterations=1).astype(np.float32) / 255.0
                except Exception:
                    alpha_eroded = alpha

                foreground = alpha_eroded > 0.1
                if np.any(foreground):
                    mean_r = np.mean(image_f[foreground, 0]) if np.any(foreground) else 128
                    mean_g = np.mean(image_f[foreground, 1]) if np.any(foreground) else 128
                    mean_b = np.mean(image_f[foreground, 2]) if np.any(foreground) else 128
                else:
                    mean_r, mean_g, mean_b = 128, 128, 128

                decontam_strength = (1.0 - alpha)[transition, np.newaxis] * 0.3
                neutral = np.array([mean_r, mean_g, mean_b], dtype=np.float32)
                image_f[transition] = image_f[transition] * (1 - decontam_strength) + neutral * decontam_strength

        halo_mask = ((alpha > 0.05) & (alpha < 0.5)).astype(np.float32)
        if np.any(halo_mask > 0):
            darken = 1.0 - (1.0 - alpha) * 0.15
            image_f = image_f * np.where(halo_mask[:, :, np.newaxis] > 0, darken[:, :, np.newaxis], 1.0)

        image_u8 = np.clip(image_f, 0, 255).astype(np.uint8)
        alpha_u8 = (alpha * 255).astype(np.uint8)

        rgba = np.dstack([image_u8, alpha_u8])
        return rgba


from io import BytesIO


def _rasterize_alpha(source: np.ndarray, alpha: np.ndarray) -> bytes:
    h, w = alpha.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, :3] = source
    rgba[:, :, 3] = alpha
    buf = BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    return buf.getvalue()
