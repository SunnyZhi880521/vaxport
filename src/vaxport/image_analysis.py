"""图像 AI 分析 — 阿里百炼 DashScope qwen-VL 模型

支持:
- HPLC 色谱图判读
- 电泳条带/SDS-PAGE 分析
- 电镜图像 (TEM/SEM) 分析
- 通用实验图像描述

API: 阿里百炼 DashScope (qwen-vl-max / qwen-vl-plus)
鉴权: DASHSCOPE_API_KEY 环境变量
"""

import base64
import json
import os
import mimetypes
from typing import Optional


def _get_client():
    """惰性初始化 DashScope 客户端。"""
    try:
        import dashscope
        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        if not api_key:
            return None, "DASHSCOPE_API_KEY 未设置"
        return dashscope, None
    except ImportError:
        return None, "dashscope 未安装，请执行: pip install dashscope"


def analyze_image(image_path: str, analysis_type: str = "general",
                  options: str = "{}") -> dict:
    """图像 AI 分析入口。

    Args:
        image_path: 图像文件路径 (支持 PNG/JPG/TIFF/BMP)
        analysis_type: 分析类型
            - "general": 通用描述
            - "hplc": HPLC 色谱图 — 识别峰、保留时间、峰面积
            - "electrophoresis": 电泳条带/SDS-PAGE — 条带分子量、纯度
            - "microscopy": 电镜图像/TEM/SEM — 细胞/病毒形态
            - "assay": 实验板/酶标板 — 孔内反应判定
        options: JSON {"model": "qwen-vl-max", "language": "zh", "detail_level": "high"}

    Returns:
        {"analysis": str, "analysis_type": str, "model": str}
    """
    dashscope, err = _get_client()
    if err:
        return {"error": err}

    try:
        opts = json.loads(options) if isinstance(options, str) else options
    except (json.JSONDecodeError, TypeError):
        opts = {}

    model = opts.get("model", "qwen-vl-max")
    language = opts.get("language", "zh")
    detail_level = opts.get("detail_level", "high")

    # 检查文件存在
    if not os.path.exists(image_path):
        return {"error": f"图像文件不存在: {image_path}"}

    # 读取并编码图像
    try:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        return {"error": f"读取图像文件失败: {e}"}

    # 推断 MIME 类型
    mime_type, _ = mimetypes.guess_type(image_path)
    if mime_type is None:
        mime_type = "image/png"
    if mime_type not in ("image/png", "image/jpeg", "image/jpg",
                          "image/tiff", "image/bmp", "image/webp"):
        mime_type = "image/png"

    # 构建分析提示词
    prompt = _build_analysis_prompt(analysis_type, language, detail_level)

    # 调用 DashScope
    try:
        resp = dashscope.MultiModalConversation.call(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"image": f"data:{mime_type};base64,{image_data}"},
                    {"text": prompt},
                ],
            }],
        )

        if resp.status_code == 200:
            analysis = resp.output.choices[0].message.content[0]["text"]
            return {
                "analysis": analysis,
                "analysis_type": analysis_type,
                "model": model,
                "image_path": image_path,
                "status": "success",
            }
        else:
            return {
                "error": f"DashScope API 错误: {resp.code} - {resp.message}",
                "status_code": resp.status_code,
            }
    except Exception as e:
        return {"error": f"API 调用失败: {e}"}


def analyze_image_batch(image_paths: str, analysis_type: str = "general",
                        options: str = "{}") -> dict:
    """批量图像分析。

    Args:
        image_paths: JSON 数组 ["path1.png", "path2.png"]
        analysis_type: 分析类型
        options: 选项 JSON

    Returns:
        {"results": [...], "total": N, "successful": N}
    """
    try:
        paths = json.loads(image_paths) if isinstance(image_paths, str) else image_paths
    except (json.JSONDecodeError, TypeError):
        return {"error": "image_paths 需为 JSON 数组"}

    results = []
    successful = 0

    for path in paths:
        result = analyze_image(path, analysis_type, options)
        results.append(result)
        if "error" not in result:
            successful += 1

    return {
        "results": results,
        "total": len(paths),
        "successful": successful,
        "analysis_type": analysis_type,
    }


def _build_analysis_prompt(analysis_type: str, language: str,
                           detail_level: str) -> str:
    """构建分析提示词"""
    lang_hint = "请用中文回答" if language == "zh" else "Please respond in English"

    prompts = {
        "general": (
            f"请详细描述这张图像的内容。{lang_hint}。"
            + ("请尽可能详细地描述所有可见的细节。" if detail_level == "high" else "")
        ),
        "hplc": (
            f"你是一位色谱分析专家。请分析这张HPLC色谱图。{lang_hint}。\n"
            "请识别:\n"
            "1. 主要色谱峰及其保留时间\n"
            "2. 峰面积和相对峰面积比\n"
            "3. 基线是否平稳\n"
            "4. 分离度是否良好（相邻峰是否完全分离）\n"
            "5. 是否有拖尾峰或前沿峰\n"
            "6. 系统适用性是否符合要求\n"
            "7. 整体色谱图质量评价"
        ),
        "electrophoresis": (
            f"你是一位生物化学分析专家。请分析这张电泳图(SDS-PAGE/琼脂糖凝胶)。{lang_hint}。\n"
            "请识别:\n"
            "1. 泳道数量和各泳道样品\n"
            "2. 可见条带及其大致分子量(kDa)\n"
            "3. 条带强度和纯度（是否有杂带）\n"
            "4. 目标蛋白条带占比\n"
            "5. Marker/标准品的条带分布\n"
            "6. 各泳道间的可比性\n"
            "7. 整体实验结果评价"
        ),
        "microscopy": (
            f"你是一位电镜分析专家。请分析这张电镜图像(TEM/SEM)。{lang_hint}。\n"
            "请识别:\n"
            "1. 图像类型(TEM/SEM)和放大倍数推断\n"
            "2. 细胞/病毒/颗粒的形态特征\n"
            "3. 大小尺寸估计(参考标尺如可见)\n"
            "4. 是否有异常形态或结构\n"
            "5. 颗粒/细胞密度和分布\n"
            "6. 整体图像质量评价"
        ),
        "assay": (
            f"你是一位生物测定专家。请分析这张实验板/酶标板图像。{lang_hint}。\n"
            "请识别:\n"
            "1. 板类型(96孔/48孔等)\n"
            "2. 各孔的显色/荧光情况\n"
            "3. 阳性/阴性对照孔的位置和结果\n"
            "4. 是否存在梯度变化\n"
            "5. 可能的实验结果判读"
        ),
    }

    return prompts.get(analysis_type, prompts["general"])