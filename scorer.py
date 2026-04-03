"""
Image scoring functions.
Each returns a float score in its defined range.
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image

import config


# ─── Text Match (0–5) ────────────────────────────────────────────────────────

def preprocess_for_ocr(image_path: Path) -> np.ndarray:
    """Convert image to grayscale + threshold for better OCR."""
    img = cv2.imread(str(image_path))
    if img is None:
        raise RuntimeError(f"Could not read image: {image_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Adaptive threshold for uneven lighting
    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=11,
        C=2,
    )
    return thresh


def score_text_match(image_path: Path, target_phrase: str) -> tuple[float, str]:
    """
    OCR the image and fuzzy-match against target phrase.
    Returns (score, raw_ocr_text).
    Score: 0–5 based on token-level fuzzy match.
    """
    try:
        processed = preprocess_for_ocr(image_path)
        # Run tesseract
        custom_config = r"--oem 3 --psm 6"
        text: str = pytesseract.image_to_string(
            Image.fromarray(processed),
            config=custom_config,
        )
    except Exception as e:
        return 0.0, f"[OCR_ERROR] {e}"

    text = text.strip()
    # Normalize: uppercase, remove extra whitespace
    normalized = re.sub(r"\s+", " ", text.upper()).strip()

    score = _fuzzy_text_score(normalized, target_phrase.upper())
    return score, text


def _fuzzy_text_score(text: str, target: str) -> float:
    """
    Score 0–5 for text match.
    Checks for exact phrase, individual tokens, and substring.
    """
    from rapidfuzz import fuzz, process

    if target in text:
        # Exact phrase match
        return 5.0

    target_tokens = target.split()
    matched_tokens = sum(1 for t in target_tokens if t in text)
    token_ratio = matched_tokens / len(target_tokens)

    # fuzzy ratio on whole string
    fuzzy_ratio = fuzz.ratio(text, target) / 100.0

    # partial ratio for substring-like matching
    partial = fuzz.partial_ratio(text, target) / 100.0

    best = max(token_ratio * 4.0, fuzzy_ratio * 3.0, partial * 3.5)
    return min(5.0, round(best, 2))


# ─── Physical Scene (0–3) ───────────────────────────────────────────────────

def score_physical_scene(image_path: Path) -> float:
    """
    Score physical scene realism 0–3.
    Uses Sobel edge density + depth-of-field heuristics.
    High edge density + natural noise = real environment.
    """
    img = cv2.imread(str(image_path))
    if img is None:
        return 0.0

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Sobel gradient magnitude
    sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobely = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    magnitude = np.sqrt(sobelx**2 + sobely**2)

    # Edge density: what fraction of pixels have significant edges
    edge_density = np.mean(magnitude > 30)
    edge_score = min(edge_density * 20, 1.0)  # 0–1

    # Laplacian variance (sharpness / depth)
    lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()

    # Natural photos have mid-range Laplacian variance
    # Blurry screen shots have very low variance
    # Very sharp synthetic images have very high variance
    if lap_var < 50:
        sharpness_score = 0.0
    elif lap_var < 500:
        sharpness_score = 0.5
    elif lap_var < 3000:
        sharpness_score = 1.0
    else:
        sharpness_score = 0.7  # suspiciously sharp

    # Color histogram entropy — real scenes have diverse colors
    color = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([color], [0], None, [256], [0, 256])
    hist = hist.flatten() / hist.sum()
    entropy = -np.sum(hist * np.log2(hist + 1e-10))

    color_score = min(entropy / 6.0, 1.0)  # 0–1, natural ~4-6 bits

    total = edge_score + sharpness_score + color_score
    return min(3.0, round(total, 2))


# ─── Image Quality (0–1) ────────────────────────────────────────────────────

def score_image_quality(image_path: Path) -> float:
    """
    Score image quality 0–1.
    Uses Laplacian variance (blur detection).
    Reject blurry / heavily compressed images.
    """
    img = cv2.imread(str(image_path))
    if img is None:
        return 0.0

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()

    if lap_var < 30:
        return 0.0
    elif lap_var < 100:
        return 0.3
    elif lap_var < 300:
        return 0.7
    elif lap_var < 1000:
        return 1.0
    else:
        return 1.0  # high quality


# ─── Anti-Screen Detection (0–1) ────────────────────────────────────────────

def score_anti_screen(image_path: Path) -> float:
    """
    Score 0–1: is this image from a real camera (1) or a screen capture (0)?
    
    Checks:
    1. EXIF presence — camera photos have EXIF, screenshots don't
    2. Moire / interference patterns — screen subpixel grid causes moire
    3. Color histogram uniformity — screens have flat uniform fills
    
    Returns weighted composite score.
    """
    img = cv2.imread(str(image_path))
    if img is None:
        return 0.0

    score = 0.0

    # 1. EXIF check
    try:
        pil_img = Image.open(image_path)
        exif = pil_img._getexif()  # type: ignore
        if exif:
            score += 0.4  # has EXIF = real camera
    except Exception:
        pass  # no EXIF = likely screen

    # 2. Moire detection via frequency analysis
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # FFT to detect regular grid patterns (subpixel screen pattern)
    fft = np.fft.fft2(gray)
    fft_shift = np.fft.fftshift(fft)
    magnitude = np.log(np.abs(fft_shift) + 1e-10)

    # Look for bright spots away from center (regular patterns)
    center_h, center_w = h // 2, w // 2
    # Check quadrant rings for high-frequency energy
    cy, cx = np.ogrid[:h, :w]
    dist = np.sqrt((cx - center_w)**2 + (cy - center_h)**2)

    # Ratio of energy in mid-frequencies (where screen grid shows)
    inner_mask = dist < h * 0.1
    outer_mask = dist > h * 0.4
    mid_mask = ~(inner_mask | outer_mask)

    total_energy = np.sum(magnitude)
    mid_energy = np.sum(magnitude[mid_mask])
    mid_ratio = mid_energy / (total_energy + 1e-10)

    # Screen grid shows up as high mid-frequency energy
    if mid_ratio < 0.15:
        score += 0.3  # low mid-freq = looks like real photo
    elif mid_ratio < 0.30:
        score += 0.15
    # else: screen-like — add 0

    # 3. Color variance check — screens often have flat colored regions
    b_channel = img[:, :, 0].astype(np.float32)
    g_channel = img[:, :, 1].astype(np.float32)
    r_channel = img[:, :, 2].astype(np.float32)

    # Screen captures often have very uniform background fills
    color_std = np.std([b_channel.mean(), g_channel.mean(), r_channel.mean()])

    # Natural photos have more color diversity
    if color_std > 5:
        score += 0.3
    elif color_std > 2:
        score += 0.15

    return min(1.0, round(score, 2))
