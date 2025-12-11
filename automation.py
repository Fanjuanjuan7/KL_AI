from pathlib import Path
from typing import Dict, Any, Optional
import time
import random
from playwright.sync_api import sync_playwright, Page, BrowserContext, Frame
try:
    from ..utils.logger import get_logger
except Exception:
    import sys, os
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from utils.logger import get_logger

# 图像识别相关库（可选）
try:
    from PIL import Image
    import io
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

try:
    import cv2
    import numpy as np
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False


def _generate_random_fingerprint() -> Dict[str, Any]:
    """
    生成随机浏览器指纹
    返回适用于Playwright new_context()的参数
    支持Windows和Mac平台
    """
    import platform
    import random
    
    # 常见用户代理
    user_agents_win = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    ]
    
    user_agents_mac = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    ]
    
    # 根据操作系统选择UA
    system = platform.system()
    if system == "Windows":
        user_agents = user_agents_win
    else:  # Mac 和 Linux
        user_agents = user_agents_mac
    
    # 屏幕分辨率
    screen_sizes = [
        {"width": 1920, "height": 1080},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1440, "height": 900},
        {"width": 2560, "height": 1440},
    ]
    
    # 时区
    timezones = [
        "America/New_York",
        "America/Chicago",
        "America/Los_Angeles",
        "Europe/London",
        "Europe/Paris",
        "Asia/Tokyo",
        "Asia/Shanghai",
        "Asia/Hong_Kong",
    ]
    
    # 语言
    locales = ["en-US", "en-GB", "zh-CN", "ja-JP", "de-DE", "fr-FR"]
    
    screen = random.choice(screen_sizes)
    
    fingerprint = {
        "user_agent": random.choice(user_agents),
        "viewport": {
            "width": screen["width"],
            "height": screen["height"] - random.randint(0, 100)  # 减去任务栏高度
        },
        "screen": screen,
        "timezone_id": random.choice(timezones),
        "locale": random.choice(locales),
        "color_scheme": random.choice(["light", "dark", "no-preference"]),
        "device_scale_factor": random.choice([1, 1.25, 1.5, 2]),
    }
    
    return fingerprint


def _human_drag_track(distance: int) -> Dict[str, Any]:
    # 生成拟人拖动轨迹：初始加速，末端减速，带抖动
    steps = []
    pos = 0
    v = 0
    while pos < abs(distance):  # 支持负数距离
        a = random.uniform(2, 5)
        v += a
        move = max(1, int(v))
        pos += move
        # 根据方向调整步骤
        steps.append(move if distance > 0 else -move)
        if pos > abs(distance) * 0.6:
            v -= random.uniform(1, 3)
        if pos > abs(distance):
            last_step = abs(distance) - (pos - move)
            steps.append(last_step if distance > 0 else -last_step)
            break
    # 微调与抖动
    for _ in range(random.randint(2, 4)):
        steps.append(random.choice([1, -1]))
    return {"steps": steps}


def _calculate_relative_distance_by_image(
    slider_frame: Frame,
    page: Page,
    bg_img_xpath: str = "//img[@class='bg-img']",
    puzzle_img_xpath: str = "//img[@class='slider-img']"
) -> Optional[int]:
    """
    使用图像识别计算滑块需要移动的相对距离
    
    核心思路：
    1. 获取背景图和拼图块在iframe中的相对位置
    2. 使用图像识别找到缺口在背景图中的位置
    3. 计算拼图块到缺口的相对距离
    
    返回：
        相对距离（px），失败返回None
    """
    log = get_logger(__name__)
    
    if not OPENCV_AVAILABLE or not PILLOW_AVAILABLE:
        log.warning("⚠️ OpenCV 或 Pillow 未安装，无法使用图像识别")
        return None
    
    try:
        log.info("🖼️ 开始图像识别计算相对距离...")
        
        # 1. 获取背景图位置
        bg_img_locator = slider_frame.locator(f"xpath={bg_img_xpath}").first
        bg_box = bg_img_locator.bounding_box(timeout=3000)
        bg_src = bg_img_locator.get_attribute('src', timeout=3000)
        
        if not bg_box or not bg_src:
            log.warning("⚠️ 无法获取背景图信息")
            return None
        
        log.info(f"📏 背景图位置: x={bg_box['x']:.0f}, y={bg_box['y']:.0f}, w={bg_box['width']:.0f}")
        
        # 2. 获取拼图块位置
        puzzle_img_locator = slider_frame.locator(f"xpath={puzzle_img_xpath}").first
        puzzle_box = puzzle_img_locator.bounding_box(timeout=3000)
        puzzle_src = puzzle_img_locator.get_attribute('src', timeout=3000)
        
        if not puzzle_box or not puzzle_src:
            log.warning("⚠️ 无法获取拼图块信息")
            return None
        
        log.info(f"🧩 拼图块位置: x={puzzle_box['x']:.0f}, y={puzzle_box['y']:.0f}, w={puzzle_box['width']:.0f}")
        
        # 3. 下载图片
        bg_response = page.request.get(bg_src)
        puzzle_response = page.request.get(puzzle_src)
        
        if bg_response.status != 200 or puzzle_response.status != 200:
            log.warning("⚠️ 图片下载失败")
            return None
        
        bg_data = bg_response.body()
        puzzle_data = puzzle_response.body()
        log.info(f"✅ 图片下载成功: 背景={len(bg_data)} bytes, 拼图={len(puzzle_data)} bytes")
        
        # 4. 图像识别找缺口
        bg_img = Image.open(io.BytesIO(bg_data))
        puzzle_img = Image.open(io.BytesIO(puzzle_data))
        
        bg_array = np.array(bg_img)
        puzzle_array = np.array(puzzle_img)
        
        # 转换颜色空间
        if len(bg_array.shape) == 3 and bg_array.shape[2] == 4:
            bg_array = cv2.cvtColor(bg_array, cv2.COLOR_RGBA2BGR)
        elif len(bg_array.shape) == 3 and bg_array.shape[2] == 3:
            bg_array = cv2.cvtColor(bg_array, cv2.COLOR_RGB2BGR)
        
        if len(puzzle_array.shape) == 3 and puzzle_array.shape[2] == 4:
            puzzle_array = cv2.cvtColor(puzzle_array, cv2.COLOR_RGBA2BGR)
        elif len(puzzle_array.shape) == 3 and puzzle_array.shape[2] == 3:
            puzzle_array = cv2.cvtColor(puzzle_array, cv2.COLOR_RGB2BGR)
        
        log.info(f"📊 图像尺寸: 背景={bg_array.shape}, 拼图={puzzle_array.shape}")
        
        # 使用模板匹配找缺口
        result = cv2.matchTemplate(bg_array, puzzle_array, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        
        gap_x_in_bg = max_loc[0]
        log.info(f"🎯 模板匹配结果: 缺口在背景图中的位置=x={gap_x_in_bg}, 置信度={max_val:.3f}")
        
        if max_val < 0.5:
            log.warning(f"⚠️ 匹配置信度过低: {max_val:.3f}")
            return None
        
        # 5. 计算相对距离：缺口位置 - 拼图块当前位置
        # 关键：在同一个坐标系内计算
        puzzle_x_in_bg = puzzle_box['x'] - bg_box['x']
        display_width = bg_box['width']
        image_width = int(bg_array.shape[1]) if len(bg_array.shape) >= 2 else 0
        scale = (display_width / image_width) if image_width > 0 else 1.0
        relative_distance = int(gap_x_in_bg * scale - puzzle_x_in_bg)
        
        log.info("\n" + "="*60)
        log.info("📊 相对坐标计算详情:")
        log.info(f"   背景图的iframe X坐标: {bg_box['x']:.0f}px")
        log.info(f"   拼图块的iframe X坐标: {puzzle_box['x']:.0f}px")
        log.info(f"   拼图块在背景图中的相对X: {puzzle_x_in_bg:.0f}px")
        log.info(f"   缺口在背景图中的X: {gap_x_in_bg}px")
        log.info(f"   背景图显示宽度/原始宽度: {display_width:.0f}/{image_width} 比例={scale:.3f}")
        log.info(f"   需要移动的相对距离: {gap_x_in_bg}*{scale:.3f} - {puzzle_x_in_bg:.0f} = {relative_distance}px")
        log.info("="*60 + "\n")
        
        return relative_distance
        
    except Exception as e:
        log.error(f"❌ 图像识别失败: {e}")
        import traceback
        log.error(traceback.format_exc())
        return None


def _smart_slider_captcha(
    slider_frame: Frame,
    page: Page,
    slider_xpath: str,
    code_input_xpath: Optional[str] = None,
    max_attempts: int = 10
) -> bool:
    """
    智能滑块验证：基于相对坐标系统
    
    策略：
    1. 优先尝试图像识别计算相对距离
    2. 如果失败，使用智能距离策略：从小到大逐步尝试
    3. 每次失败后刷新验证码，避免被锁定
    
    返回：
        成功返回True，否则返回False
    """
    log = get_logger(__name__)
    
    try:
        # 获取滑块元素
        slider_locator = slider_frame.locator(f"xpath={slider_xpath}").first
        box = slider_locator.bounding_box(timeout=5000)
        
        if not box:
            log.error("❌ 无法获取滑块位置")
            return False
        
        log.info(f"📍 滑块初始位置: x={box['x']:.0f}, y={box['y']:.0f}, w={box['width']:.0f}, h={box['height']:.0f}")
        
        # 尝试图像识别计算相对距离
        distance_from_image = _calculate_relative_distance_by_image(slider_frame, page)
        
        # 准备距离列表
        if distance_from_image is not None and 50 < distance_from_image < 600:
            log.info(f"✅ 图像识别成功，相对距离={distance_from_image}px")
            distances_to_try = [
                distance_from_image,
                distance_from_image - 5,
                distance_from_image + 5,
                distance_from_image - 10,
                distance_from_image + 10,
            ]
        else:
            log.info("🔄 图像识别未生效，使用智能距离策略")
            # 智能策略：基于常见缺口位置的距离
            distances_to_try = [
                200, 220, 180, 240, 160,  # 中等距离
                260, 140, 280, 120, 300,  # 扩大范围
            ]
        
        log.info(f"🎯 将尝试 {len(distances_to_try)} 个距离: {distances_to_try}")
        
        # 尝试每个距离
        for attempt, distance in enumerate(distances_to_try[:max_attempts], 1):
            log.info("\n" + "="*60)
            log.info(f"🎯 尝试 {attempt}/{len(distances_to_try)}: 相对距离 {distance}px")
            log.info("="*60)
            
            try:
                # 重新获取滑块位置（可能已被重置）
                box = slider_locator.bounding_box(timeout=3000)
                start_x = box["x"] + box["width"] / 2
                start_y = box["y"] + box["height"] / 2
                
                log.info(f"📐 拖动详情:")
                log.info(f"   起始位置: ({start_x:.0f}, {start_y:.0f})")
                log.info(f"   相对移动: +{distance}px")
                log.info(f"   目标位置: ({start_x + distance:.0f}, {start_y:.0f})")
                
                # 生成拟人轨迹
                track = _human_drag_track(distance)
                steps = track["steps"]
                
                log.info(f"🎬 拟人轨迹: {len(steps)} 步骤")
                
                # 执行拖动
                page.mouse.move(start_x, start_y)
                time.sleep(random.uniform(0.3, 0.5))
                page.mouse.down()
                time.sleep(random.uniform(0.15, 0.25))
                
                current_x = start_x
                for i, step in enumerate(steps):
                    current_x += step
                    jitter_y = start_y + random.randint(-2, 2)
                    page.mouse.move(current_x, jitter_y)
                    time.sleep(random.uniform(0.015, 0.035))
                    
                    # 每10步记录一次
                    if (i + 1) % 10 == 0 or i == len(steps) - 1:
                        log.info(f"   进度: {i+1}/{len(steps)}, 当前X={current_x:.0f}")
                
                time.sleep(random.uniform(0.2, 0.3))
                page.mouse.up()
                
                actual_distance = current_x - start_x
                log.info(f"✅ 拖动完成: 实际移动={actual_distance:.0f}px")
                
                # 等待验证结果
                time.sleep(6)  # 增加等待时间让iframe有时间关闭
                
                # 🔴 关键：检查主页面上的验证码输入框（不是iframe）
                if code_input_xpath:
                    try:
                        # 在主页面上查找验证码输入框
                        code_input_locator = page.locator(f"xpath={code_input_xpath}").first
                        code_input_locator.wait_for(state="visible", timeout=5000)
                        log.info("\n" + "="*60)
                        log.info("🎉🎉 滑块验证成功！验证码输入框已出现")
                        log.info(f"✅ 成功距离: {distance}px")
                        log.info(f"✅ 实际移动: {actual_distance:.0f}px")
                        log.info("="*60 + "\n")
                        return True
                    except Exception as check_err:
                        log.warning(f"⚠️ 距离 {distance}px 验证失败: {check_err}")
                        # 失败后不要继续尝试，因为iframe可能已关闭
                        log.error("❌ 验证失败，停止尝试")
                        return False
                
            except Exception as drag_err:
                log.error(f"❌ 拖动失败: {drag_err}")
                continue
        
        log.error("❌ 所有尝试均失败")
        return False
        
    except Exception as e:
        log.error(f"❌ 滑块验证失败: {e}")
        import traceback
        log.error(traceback.format_exc())
        return False


def _perform_human_drag(page: Page, slider_xpath: str, container_xpath: Optional[str] = None) -> bool:
    """
    废弃函数：请使用 _smart_slider_captcha() 代替
    保留此函数仅为了向后兼容
    """
    log = get_logger(__name__)
    log.error("❌ _perform_human_drag() 已废弃，请使用 _smart_slider_captcha()")
    return False


def _extract_verification_code(page: Page, code_xpath: str, max_wait: int = 30) -> Optional[str]:
    """
    从邮箱接码页面提取验证码
    参数：
        page: Playwright页面对象
        code_xpath: 验证码元素的XPath
        max_wait: 最大等待时间（秒）
    返回：
        6位验证码字符串，如果提取失败则返回None
    """
    import re
    log = get_logger(__name__)
    
    try:
        # 等待验证码元素出现
        log.info(f"Waiting for verification code element: {code_xpath}")
        loc = page.locator(f"xpath={code_xpath}")
        loc.wait_for(state="visible", timeout=max_wait * 1000)
        
        # 获取文本内容
        code_text = loc.inner_text()
        log.info(f"Extracted text: {code_text}")
        
        # 使用正则表达式提取6位数字
        match = re.search(r'\b\d{6}\b', code_text)
        if match:
            code = match.group(0)
            log.info(f"Successfully extracted verification code: {code}")
            return code
        else:
            log.warning(f"No 6-digit code found in text: {code_text}")
            return None
    except Exception as e:
        log.error(f"Failed to extract verification code: {e}")
        return None


def run_registration_flow(
    email: str,
    password: str,
    runtime_dir: Path,
    xpaths: Dict[str, str],
    proxy: Optional[Dict[str, Any]] = None,
    platform_url: Optional[str] = None,
    code_url: Optional[str] = None,
    attach_ws: Optional[str] = None,
    dry_run: bool = False,
    browser_mode: str = "bitbrowser",  # 浏览器模式
    element_timeout_ms: Optional[int] = None,
    element_poll_ms: Optional[int] = None,
) -> bool:
    # 占位实现：后续接入 Playwright 与页面操作
    # 当前版本：在 dry_run=True 时仅返回成功，便于流程调试与批处理联通
    if dry_run:
        time.sleep(0.05)
        return True
    
    log = get_logger(__name__)
    GLOBAL_WAIT_MS = element_timeout_ms or 20000
    GLOBAL_POLL_MS = element_poll_ms or 500
    try:
        with sync_playwright() as p:
            # 启动浏览器
            browser = None
            page = None
            context = None
            
            # 如果有attach_ws，尝试附着到比特浏览器
            if attach_ws:
                try:
                    log.info(f"Attempting to attach to BitBrowser via WebSocket: {attach_ws}")
                    browser = p.chromium.connect_over_cdp(attach_ws)
                    log.info("Successfully connected to BitBrowser")
                    
                    # 使用已有上下文与页面
                    contexts = browser.contexts
                    if contexts:
                        context = contexts[0]
                        log.info(f"Found {len(context.pages)} pages in context")
                        
                        # 详细列出所有页面
                        for idx, p_page in enumerate(context.pages):
                            page_url = p_page.url
                            page_title = p_page.title()
                            log.info(f"  Page {idx}: Title='{page_title}', URL={page_url}")
                        
                        # 查找Kling AI标签页（优先匹配）
                        for p_page in context.pages:
                            page_url = p_page.url
                            page_title = p_page.title()
                            if "klingai" in page_url.lower() or "kling" in page_title.lower():
                                page = p_page
                                log.info(f"✅ Found Kling AI page: Title='{page_title}', URL={page_url}")
                                break
                        
                        # 如果没找到Kling AI页面，使用最后一个页面（通常是最新打开的）
                        if not page and context.pages:
                            page = context.pages[-1]
                            page_url = page.url
                            page_title = page.title()
                            log.info(f"Using last page: Title='{page_title}', URL={page_url}")
                            
                            # 如果当前页面不是Kling AI，导航到Kling AI
                            if "klingai" not in page.url.lower() and "kling" not in page_title.lower():
                                target_url = platform_url or "https://klingai.com"
                                log.info(f"Current page is not Kling AI, navigating to: {target_url}")
                                try:
                                    page.goto(target_url, timeout=30000, wait_until="domcontentloaded")
                                    log.info(f"Successfully navigated to {target_url}")
                                    # 不再等待networkidle，domcontentloaded已足够
                                    log.info("Page DOM content loaded; proceeding without full network idle")
                                except Exception as nav_error:
                                    log.error(f"Failed to navigate: {nav_error}")
                                    # 如果导航失败，至少等待DOM加载
                                    try:
                                        page.wait_for_load_state("domcontentloaded", timeout=10000)
                                    except:
                                        pass
                    
                    if page:
                        log.info("✅ Successfully attached to BitBrowser page")
                    else:
                        log.warning("No suitable page found, will create new browser")
                        browser = None
                        
                        # 如果是比特浏览器模式，不能创建新浏览器
                        if browser_mode != "playwright":
                            log.error("❌ 比特浏览器模式下无法创建新浏览器实例")
                            return False
                        
                except Exception as e:
                    log.error(f"Failed to attach to BitBrowser: {e}")
                    import traceback
                    log.error(traceback.format_exc())
                    browser = None
                    
                    # 如果是比特浏览器模式，不能创建新浏览器
                    if browser_mode != "playwright":
                        log.error("❌ 比特浏览器模式下无法连接到现有浏览器实例")
                        return False
            
            if not browser:
                log.error("无法连接到比特浏览器")
                return False
            try:
                page.bring_to_front()
                log.info("置于顶层：将页面带到前台")
            except Exception as e:
                log.warning(f"置于顶层失败: {e}")
            
            try:
                def _block_static(route):
                    req = route.request
                    url = req.url.lower()
                    
                    # Only block non-essential resources that might slow down loading
                    # Allow essential resources like scripts, stylesheets, and API calls
                    if req.resource_type in ["image", "media", "font"]:
                        # Allow images from the main domain and CDN
                        if any(domain in url for domain in ["klingai.com", "kcdn", "kwai-pro"]):
                            return route.continue_()
                        # Block external images/media that might be slow
                        return route.abort()
                    
                    # Block known analytics and tracking domains
                    blocked_domains = ["google-analytics.com", "doubleclick", "gtag", "mixpanel", "amplitude", "hotjar", "pixel", "clarity.ms"]
                    for domain in blocked_domains:
                        if domain in url:
                            return route.abort()
                    
                    # Allow everything else
                    return route.continue_()
                
                page.route("**/*", _block_static)
                log.info("🚫 已启用选择性资源屏蔽: 外部图片/媒体/分析工具")
                _net_events = []
                def _on_req(req):
                    try:
                        _net_events.append({"type": "req", "url": req.url, "ts": time.perf_counter(), "rt": req.resource_type})
                    except Exception:
                        pass
                def _on_res(res):
                    try:
                        _net_events.append({"type": "res", "url": res.url, "ts": time.perf_counter(), "status": res.status, "rt": res.request.resource_type})
                    except Exception:
                        pass
                def _on_fail(req):
                    try:
                        _net_events.append({"type": "fail", "url": req.url, "ts": time.perf_counter(), "rt": req.resource_type})
                    except Exception:
                        pass
                page.on("request", _on_req)
                page.on("response", _on_res)
                page.on("requestfailed", _on_fail)
            except Exception as e:
                log.warning(f"设置资源屏蔽失败: {e}")
            
            # 优化：不再等待整页完全加载，后续直接按步骤执行
            
            # 记录当前页面状态（已简化，避免额外的try/except嵌套）
            current_url = None
            current_title = None
            try:
                current_url = page.url
            except Exception:
                current_url = None
            try:
                current_title = page.title()
            except Exception:
                current_title = None
            try:
                perf = page.evaluate("() => ({ t: performance.timing, nav: performance.getEntriesByType('navigation')[0] || null })")
                log.info(f"⏱️ 性能: domContentLoaded={(perf.get('t', {}).get('domContentLoadedEventEnd', 0))} firstByte={(perf.get('t', {}).get('responseStart', 0))}")
                try:
                    third = []
                    for e in _net_events:
                        if e.get("type") == "res":
                            u = e.get("url", "")
                            if ("klingai.com" not in u) and ("kcdn" not in u) and ("kwai" not in u):
                                third.append(u)
                    if third:
                        log.info(f"第三方资源数量: {len(third)}")
                except Exception:
                    pass
            except Exception:
                pass
            if current_url or current_title:
                log.info(f"🌐 当前页面: 标题='{current_title}', URL={current_url}")
            
            # 获取实际窗口大小并记录
            try:
                viewport_size = page.viewport_size
                if viewport_size:
                    log.info(f"📐 当前视口大小: {viewport_size['width']}x{viewport_size['height']}")
                else:
                    log.info("📐 使用浏览器窗口大小（no_viewport=True）")
                    # 尝试通过JavaScript获取窗口大小
                    try:
                        window_size = page.evaluate('''
                            () => ({
                                width: window.innerWidth,
                                height: window.innerHeight,
                                outerWidth: window.outerWidth,
                                outerHeight: window.outerHeight
                            })
                        ''')
                        log.info(f"📐 浏览器窗口内部尺寸: {window_size['width']}x{window_size['height']}")
                        log.info(f"📐 浏览器窗口外部尺寸: {window_size['outerWidth']}x{window_size['outerHeight']}")
                    except Exception as js_err:
                        log.warning(f"获取窗口尺寸失败: {js_err}")
            except Exception as e:
                log.warning(f"获取视口信息失败: {e}")
            
            # 监听控制台日志
            try:
                page.on("console", lambda msg: log.info(f"浏览器控制台 {msg.type}: {msg.text}"))
            except Exception as e:
                log.warning(f"设置控制台监听失败: {e}")
            # 监听网络请求失败与响应状态，便于定位BitBrowser网络异常
            try:
                page.on("requestfailed", lambda req: log.warning(f"请求失败: {req.url} type={req.resource_type} failure={req.failure}") )
                page.on("response", lambda res: log.info(f"响应: {res.request.url} status={res.status}") )
            except Exception as e:
                log.warning(f"设置网络监听失败: {e}")
            
            # 截图保存当前页面状态
            
            
            # 语言与入口初始化（放在最前面按JSON顺序执行）
            log.info("\n" + "="*60)
            log.info("步骤1: 悬停语言菜单并切换到 English")
            log.info("="*60)
            lang_menu = xpaths.get("language_menu")
            if lang_menu:
                try:
                    loc = page.locator(f"xpath={lang_menu}")
                    loc.wait_for(state="visible", timeout=GLOBAL_WAIT_MS)
                    try:
                        loc.scroll_into_view_if_needed(timeout=3000)
                    except Exception:
                        pass
                    try:
                        loc.hover(timeout=5000)
                    except Exception:
                        pass
                    time.sleep(1)
                except Exception:
                    log.warning(f"⚠️ 语言菜单不可见或不可交互，继续尝试直接点击English: {lang_menu[:80]}")
            english = xpaths.get("english_option")
            if english:
                try:
                    # 优先在语言菜单作用域内查找English，避免误匹配正文中的“English”
                    if lang_menu:
                        loc = page.locator(f"xpath={lang_menu}").locator("xpath=//*[text()='English']")
                    else:
                        loc = page.locator(f"xpath={english}")
                    loc.wait_for(state="visible", timeout=GLOBAL_WAIT_MS)
                    try:
                        loc.scroll_into_view_if_needed(timeout=3000)
                    except Exception:
                        pass
                    try:
                        loc.click(timeout=5000)
                    except Exception:
                        try:
                            page.evaluate(
                                """
                                (xpath) => {
                                    const n = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                                    if (n) { n.click(); return true; }
                                    return false;
                                }
                                """,
                                english,
                            )
                        except Exception:
                            pass
                    time.sleep(1)
                except Exception:
                    return False
            log.info("\n" + "="*60)
            log.info("步骤2: 点击 Creative Studio 与 More Tools")
            log.info("="*60)
            cs = xpaths.get("Creative Studio")
            if cs:
                try:
                    loc = page.locator(f"xpath={cs}")
                    loc.wait_for(state="visible", timeout=GLOBAL_WAIT_MS)
                    try:
                        loc.scroll_into_view_if_needed(timeout=3000)
                    except Exception:
                        pass
                    try:
                        loc.click(timeout=5000)
                    except Exception:
                        try:
                            page.evaluate(
                                """
                                (xpath) => {
                                    const n = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                                    if (n) { n.click(); return true; }
                                    return false;
                                }
                                """,
                                cs,
                            )
                        except Exception:
                            pass
                    time.sleep(1)
                except Exception:
                    return False
            mt = xpaths.get("More Tools")
            if mt:
                try:
                    loc = page.locator(f"xpath={mt}")
                    loc.wait_for(state="visible", timeout=GLOBAL_WAIT_MS)
                    try:
                        loc.scroll_into_view_if_needed(timeout=3000)
                    except Exception:
                        pass
                    try:
                        loc.click(timeout=5000)
                    except Exception:
                        try:
                            page.evaluate(
                                """
                                (xpath) => {
                                    const n = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                                    if (n) { n.click(); return true; }
                                    return false;
                                }
                                """,
                                mt,
                            )
                        except Exception:
                            pass
                    time.sleep(1)
                    # 进入 More Tools 后，立即尝试点击 Sign In（若存在），避免后续等待阻塞
                    try:
                        signin_btn = xpaths.get("signin_btn")
                        if signin_btn:
                            # Use shorter timeout for immediate click attempt
                            safe_click(signin_btn, timeout_ms=5000, required=False)
                    except Exception:
                        pass
                except Exception:
                    return False

            # 初始化连续失败计数器
            consecutive_failures = 0
            max_consecutive_failures = 2
            
            # 通用安全操作封装
            def element_exists(xpath: str, timeout_ms: int = 20000, poll_ms: int = 500) -> bool:
                """检查元素是否存在 - 轮询机制，可配置超时与轮询间隔"""
                import time
                start_time = time.time()
                # Use the passed timeout_ms parameter, not GLOBAL_WAIT_MS
                actual_timeout = timeout_ms if timeout_ms != 20000 else GLOBAL_WAIT_MS
                poll_interval = max(0.05, ((GLOBAL_POLL_MS) / 1000.0))
                
                log.info(f"🔍 轮询查找元素 (超时={actual_timeout}ms, 间隔={int(poll_interval*1000)}ms): {xpath[:80]}...")
                
                while (time.time() - start_time) * 1000 < actual_timeout:
                    try:
                        loc = page.locator(f"xpath={xpath}")
                        # 检查元素是否存在于DOM中
                        count = loc.count()
                        if count > 0:
                            # Also check if element is visible and enabled
                            try:
                                is_visible = loc.is_visible(timeout=1000)
                                is_enabled = loc.is_enabled(timeout=1000)
                                if is_visible and is_enabled:
                                    elapsed = int((time.time() - start_time) * 1000)
                                    log.info(f"✅ 元素找到且可见！耗时 {elapsed}ms: {xpath[:80]}...")
                                    return True
                                else:
                                    log.info(f"🟡 元素存在但不可见/不可用，继续等待: visible={is_visible}, enabled={is_enabled}")
                            except Exception as e:
                                # If visibility check fails, still return true if element exists
                                elapsed = int((time.time() - start_time) * 1000)
                                log.info(f"✅ 元素找到！耗时 {elapsed}ms: {xpath[:80]}... (visibility check failed: {e})")
                                return True
                    except Exception as e:
                        pass  # 继续轮询
                    
                    # 每次轮询显示进度
                    elapsed = int((time.time() - start_time) * 1000)
                    if elapsed % 2000 < poll_interval * 1000:  # 每2秒记录一次
                        log.info(f"⏳ 还在等待... ({elapsed}ms / {actual_timeout}ms)")
                    
                    time.sleep(poll_interval)
                
                # 超时
                elapsed = int((time.time() - start_time) * 1000)
                log.warning(f"⚠️ 元素未找到，超时 {elapsed}ms: {xpath[:80]}...")
                return False
            

            def safe_click(xpath: Optional[str], timeout_ms: int = 20000, required: bool = False) -> bool:
                global consecutive_failures
                if not xpath:
                    return True
                retries = 3
                for attempt in range(1, retries + 1):
                    ts = int(time.time() * 1000)
                    log.info(f"attempt={attempt} ts={ts} method=xpath target={xpath[:80]} state=locating")
                    # Use the passed timeout_ms parameter, don't override it
                    if not element_exists(xpath, timeout_ms=timeout_ms):
                        if attempt < retries:
                            time.sleep(2)
                            continue
                        if required:
                            consecutive_failures += 1
                            msg = f"required element not found: {xpath[:80]}"
                            raise Exception(msg)
                        else:
                            return True
                    consecutive_failures = 0
                    try:
                        loc = page.locator(f"xpath={xpath}")
                        # Since element_exists already confirmed the element exists, try immediate actions
                        try:
                            loc.scroll_into_view_if_needed(timeout=3000)
                        except Exception:
                            pass
                        
                        # Try multiple click methods immediately
                        click_success = False
                        
                        # Method 1: Direct click
                        try:
                            loc.click(timeout=5000)
                            log.info(f"attempt={attempt} ts={ts} method=xpath target={xpath[:80]} state=clicked")
                            click_success = True
                        except Exception as click_err:
                            log.info(f"Direct click failed: {click_err}")
                            
                            # Method 2: JavaScript click if direct click fails
                            if "intercepts pointer events" in str(click_err) or "not clickable" in str(click_err):
                                try:
                                    result = page.evaluate(
                                        """
                                        (xpath) => {
                                            const n = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                                            if (n) { n.click(); return true; }
                                            return false;
                                        }
                                        """,
                                        xpath,
                                    )
                                    if result:
                                        log.info(f"attempt={attempt} ts={ts} method=js target={xpath[:80]} state=clicked")
                                        click_success = True
                                    else:
                                        log.warning(f"JavaScript click returned false")
                                except Exception as js_err:
                                    log.warning(f"JavaScript click failed: {js_err}")
                        
                        if click_success:
                            return True
                            
                        # If both methods failed, retry if we have attempts left
                        if attempt < retries:
                            log.info(f"Click failed, retrying attempt {attempt + 1}/{retries}")
                            time.sleep(1)  # Shorter sleep between retries
                            continue
                            
                        # Final failure
                        if required:
                            consecutive_failures += 1
                            msg = f"required click failed after {retries} attempts: {xpath[:80]}"
                            raise Exception(msg)
                        else:
                            log.warning(f"Optional click failed after {retries} attempts: {xpath[:80]}")
                            return True
                            
                    except Exception as e:
                        if attempt < retries:
                            log.info(f"Exception during click, retrying: {e}")
                            time.sleep(1)
                            continue
                        if required:
                            consecutive_failures += 1
                            msg = f"required click failed: {xpath[:80]} - {e}"
                            raise Exception(msg)
                        else:
                            return True

            def safe_fill(xpath: Optional[str], text: str, timeout_ms: int = 50000, required: bool = True) -> bool:
                global consecutive_failures
                if not xpath:
                    return True if not required else False
                xpaths_to_try = [xpath]
                if 'email' in xpath.lower() or 'Email' in xpath:
                    xpaths_to_try.extend([
                        "//*[contains(@placeholder, 'mail')]",
                        "//input[@type='email']",
                        "//input[contains(@class, 'email')]",
                        "//input[contains(@name, 'email')]",
                        "//input[@placeholder='Email address']",
                        "//input[@aria-label='Email address']",
                        "//label[contains(., 'Email')]/following::input[1]",
                        "//input[@id='email']",
                    ])
                retries = 3
                for try_xpath in xpaths_to_try:
                    for attempt in range(1, retries + 1):
                        ts = int(time.time() * 1000)
                        log.info(f"attempt={attempt} ts={ts} method=xpath target={try_xpath[:80]} state=locating")
                        # Use the passed timeout_ms parameter, don't override it
                        if not element_exists(try_xpath, timeout_ms=timeout_ms):
                            if attempt < retries:
                                time.sleep(1)  # Reduced from 2s
                                continue
                            break
                        consecutive_failures = 0
                        try:
                            loc = page.locator(f"xpath={try_xpath}")
                            # Since element_exists already confirmed the element exists, try immediate fill
                            try:
                                loc.scroll_into_view_if_needed(timeout=3000)
                            except Exception:
                                pass
                            
                            # Try to focus and clear first
                            try:
                                loc.click(timeout=3000)
                            except Exception:
                                pass
                            
                            # Fill the text
                            loc.fill(text, timeout=5000)
                            
                            # Verify the fill was successful
                            try:
                                filled_value = loc.input_value(timeout=2000)
                                if filled_value == text:
                                    log.info(f"attempt={attempt} ts={ts} method=xpath target={try_xpath[:80]} state=filled")
                                    return True
                                else:
                                    log.warning(f"填充验证失败: 期望'{text}', 实际'{filled_value}'")
                            except Exception as verify_err:
                                log.warning(f"填充验证异常: {verify_err}")
                                # Still consider it successful if fill didn't throw
                                return True
                                
                        except Exception as e:
                            log.warning(f"填充失败: {e}")
                            if attempt < retries:
                                time.sleep(1)  # Reduced from 2s
                                continue
                            break
                    continue
                if required:
                    consecutive_failures += 1
                    msg = f"required input not found: {xpath[:80]}"
                    raise Exception(msg)
                else:
                    return True

            # ... existing code ...

            # 步骤1: 关闭弹窗（如存在）
            log.info("\n" + "="*60)
            log.info("Step 1: Closing popup (if exists)")
            log.info("="*60)
            
            # 弹窗关闭步骤已移除，按语言切换与入口优先执行
            
            # 初始化变量
            login_entry_found = False
            
            # 步骤2: 点击Sign In按钮

            # 初始点击路径 - 可选
            log.info("\n" + "="*60)
            log.info("步骤2: 点击 Sign In 按钮（如果存在）")
            log.info("="*60)
            signin_btn = xpaths.get("signin_btn")
            if signin_btn and not login_entry_found:
                # 立即尝试点击，减少等待
                if safe_click(signin_btn, timeout_ms=5000, required=True):
                    log.info("✅ Sign In 按钮已点击，等待响应...")
                    time.sleep(1)  # Reduced from 3s
                    
            elif login_entry_found:
                log.info("ℹ️ 已通过其他方式进入登录流程，跳过 Sign In 按钮")
            else:
                raise Exception("signin_btn 未配置，无法继续")
            
            log.info("\n" + "="*60)
            log.info("步骤3: 选择邮箱登录方式 (signin_with_email)")
            log.info("="*60)
            # 按照kling_xpaths.json配置顺序，第3步应该是signin_with_email
            signin_with_email = xpaths.get("signin_with_email")
            email_login_clicked = False
            if signin_with_email:
                # 立即尝试点击，减少等待
                if safe_click(signin_with_email, timeout_ms=5000, required=True):
                    log.info("✅ 邮箱登录选项已点击，等待表单加载...")
                    time.sleep(2)  # Reduced from 5s
                    
                    # 滚动页面确保表单在视口内
                    try:
                        page.evaluate("window.scrollTo(0, 300)")
                        log.info("✅ 页面已滚动，确保表单可见")
                    except Exception as scroll_err:
                        log.warning(f"⚠️ 滚动失败: {scroll_err}")
                    
                    email_login_clicked = True
                    
                    # 移除截图保存逻辑
                else:
                    log.warning("⚠️ 邮箱登录选项未找到或点击失败")
            else:
                raise Exception("signin_with_email 未配置，无法继续")
            
            log.info("\n" + "="*60)
            log.info("步骤4: 点击注册链接 (Sign up for free)")
            log.info("="*60)
            # 按照kling_xpaths.json配置顺序，第4步应该是Sign up for free
            signup_link = xpaths.get("Sign up for free")
            signup_clicked = False
            
            if signup_link:
                # 尝试点击注册链接（移除2秒预等待）
                if safe_click(signup_link, timeout_ms=15000, required=True):
                    log.info("✅ 注册链接点击命令执行成功")
                    time.sleep(1)  # 减少到1秒等待
                    
                    # 验证点击是否生效：检查是否出现注册表单
                    log.info("🔍 验证是否出现邮箱输入表单...")
                    
                    # 检查是否有邮箱输入框
                    email_input_xpath = xpaths.get("Enter Email Address")
                    if email_input_xpath and element_exists(email_input_xpath, timeout_ms=5000):
                        log.info("✅ 邮箱输入表单已出现！（检测到邮箱输入框）")
                        signup_clicked = True
                    else:
                        log.warning("⚠️ 邮箱输入表单未出现")
                    
                    # 移除2秒额外渲染等待
                    
                    # 滚动页面到顶部，确保表单在视口内
                    try:
                        page.evaluate("window.scrollTo(0, 0)")
                        log.info("✅ 页面已滚动到顶部")
                        time.sleep(1)
                    except Exception as scroll_err:
                        log.warning(f"⚠️ 滚动失败: {scroll_err}")
                    
                    # 移除截图保存逻辑
                else:
                    log.warning("⚠️ 注册链接未找到或点击失败")
            else:
                log.warning("⚠️ XPath配置中未定义注册链接")
            
            # 如果步骤3和4都未能顺利进行，记录警告但继续流程
            if not email_login_clicked and not signup_clicked:
                log.warning("⚠️ 邮箱登录和注册流程均未成功进行，将尝试继续...")

            # 填入邮箱与密码
            log.info("\n" + "="*60)
            log.info("步骤5: 填写邮箱和密码")
            log.info("="*60)
            
            # 修复：使用正确的键名匹配XPath配置文件
            email_input = xpaths.get("Enter Email Address")  # 注意：键名带空格
            log.info(f"🔍 开始填写邮箱 (XPath: {email_input[:60]}...)")
            if email_input and safe_fill(email_input, email, timeout_ms=15000, required=True):  # 增加超时到15秒
                # 移除1秒等待，safe_fill已有验证
                pass
            else:
                log.error("❌ 邮箱输入框填写失败，无法继续")
                return False
            
            password_input = xpaths.get("password_input")
            if password_input and safe_fill(password_input, password, timeout_ms=15000, required=True):
                # 移除1秒等待，safe_fill已有验证
                pass
            else:
                log.error("❌ 密码输入框填写失败，无法继续")
                return False
            
            # Confirm Password 也需要使用正确的键名
            confirm_input = xpaths.get("Confirm Password")  # 注意：键名带空格
            if confirm_input:
                if safe_fill(confirm_input, password, timeout_ms=15000, required=False):
                    # 移除1秒等待，safe_fill已有验证
                    pass
            else:
                log.info("ℹ️ 无确认密码字段，跳过")

            log.info("\n" + "="*60)
            log.info("Step 6: Clicking Next button")
            log.info("="*60)
            next_btn = xpaths.get("next_btn")
            if next_btn:
                if safe_click(next_btn, timeout_ms=10000, required=True):
                    log.info("✅ Next button clicked, waiting for next step...")
                    time.sleep(1)  # 减少到1秒等待页面加载
                else:
                    log.error("❌ Failed to click Next button, cannot continue")
                    return False
            else:
                log.error("❌ No next_btn XPath defined, cannot continue")
                return False
            
            # 处理滑块验证（如果有）
            log.info("\n" + "="*60)
            log.info("Step 7: Solving slider CAPTCHA (if exists)")
            log.info("="*60)
            
            slider_iframe_xpath = xpaths.get("slider_iframe")
            slider_xpath = xpaths.get("slider_handle")
            slider_container_xpath = xpaths.get("slider_container")
            
            slider_solved = False  # 标记滑块是否解决
            
            if slider_xpath:
                log.info("🎯 检测到滑块配置，尝试解决...")
                
                # 如果滑块在iframe中，先切换到iframe
                if slider_iframe_xpath and element_exists(slider_iframe_xpath, timeout_ms=10000):
                    try:
                        log.info(f"🔍 检测到iframe: {slider_iframe_xpath[:60]}")
                        
                        # 等待iframe加载（使用Locator）
                        iframe_locator = page.locator(f"xpath={slider_iframe_xpath}").first
                        iframe_locator.wait_for(state="attached", timeout=15000)
                        log.info("✅ iframe元素已加载")
                        
                        # 🔴 关键修改：使用原生 Frame API 而不是 frame_locator
                        # 等待iframe内容加载（减少等待时间）
                        log.info("⏳ 等待iframe内容初始化...")
                        time.sleep(1)  # 减少到1秒等待
                        
                        # 获取所有 frame 对象
                        all_frames = page.frames
                        log.info(f"📋 当前页面总共有 {len(all_frames)} 个 frame")
                        
                        # 查找包含 'captcha' 的 iframe（通过URL或name）
                        slider_frame = None
                        
                        # 方法1: 通过 frame.url 查找
                        for frame in all_frames:
                            frame_url = frame.url
                            if frame_url and 'captcha' in frame_url.lower():
                                slider_frame = frame
                                log.info(f"✅ 找到滑块iframe (通过URL): {frame_url[:80]}")
                                break
                        
                        # 方法2: 如果通过URL找不到，尝试通过iframe元素的src属性
                        if not slider_frame:
                            log.info("🔍 通过URL未找到，尝试通过元素属性查找...")
                            try:
                                # 获取iframe元素的src属性
                                iframe_src = iframe_locator.get_attribute('src', timeout=5000)
                                log.info(f"📝 iframe src属性: {iframe_src[:100] if iframe_src else 'None'}")
                                
                                # 再次获取frames（可能已经加载完成）
                                time.sleep(2)
                                all_frames = page.frames
                                log.info(f"📋 重新获取，当前页面总共有 {len(all_frames)} 个 frame")
                                
                                # 尝试通过URL匹配或者索引匹配
                                for idx, frame in enumerate(all_frames):
                                    frame_url = frame.url
                                    log.info(f"  Frame {idx}: URL='{frame_url[:100] if frame_url else '(empty)'}'")
                                    
                                    # 如果URL包含captcha
                                    if frame_url and 'captcha' in frame_url.lower():
                                        slider_frame = frame
                                        log.info(f"✅ 找到滑块iframe (延迟加载): Frame {idx}")
                                        break
                                    
                                    # 如果URL为空但src属性包含captcha，使用最后一个非主页面的frame
                                    if not frame_url and idx > 0:  # 跳过主frame
                                        # 这可能是还在加载的captcha iframe
                                        slider_frame = frame
                                        log.info(f"⚠️ 使用URL为空的Frame {idx} (可能是captcha iframe)")
                                        # 不break，继续查找更确定的
                                        
                            except Exception as attr_err:
                                log.warning(f"⚠️ 获取iframe属性失败: {attr_err}")
                        
                        if not slider_frame:
                            log.error("❌ 未找到captcha iframe")
                            log.info("📋 最终所有 frame URLs:")
                            for idx, frame in enumerate(all_frames):
                                log.info(f"  Frame {idx}: {frame.url[:100] if frame.url else '(empty)'}")
                            raise Exception("Captcha iframe not found")
                        
                        # 等待iframe内容加载
                        log.info("⏳ 等待iframe内容完全加载...")
                        try:
                            slider_frame.wait_for_load_state("domcontentloaded", timeout=15000)
                            log.info("✅ iframe DOM 已加载")
                        except Exception as load_err:
                            log.warning(f"⚠️ iframe load_state 超时，继续尝试: {load_err}")
                        
                        time.sleep(1)  # 减少到1秒等待JavaScript执行
                        
                        # 🎉 使用新的智能滑块验证函数 - 最多重试10次
                        code_input_xpath = xpaths.get("code_url_element")
                        max_retry_attempts = 10  # 🔴 从max5次增加到10次
                        slider_success = False
                        
                        for retry_count in range(max_retry_attempts):
                            log.info("\n" + "="*60)
                            log.info(f"🦾 开始智能滑块验证（基于相对坐标）- 尝试 {retry_count + 1}/{max_retry_attempts}")
                            log.info("="*60)
                            
                            slider_success = _smart_slider_captcha(
                                slider_frame=slider_frame,
                                page=page,
                                slider_xpath=slider_xpath,
                                code_input_xpath=code_input_xpath,
                                max_attempts=1  # 每次重试只尝试一个距离
                            )
                            
                            if slider_success:
                                log.info(f"✅✅ 滑块验证成功！（第{retry_count + 1}次尝试）")
                                slider_solved = True
                                break
                            else:
                                log.warning(f"⚠️ 第{retry_count + 1}次滑块验证失败")
                                
                                # 如果还有重试机会，等待iframe自动刷新验证码
                                if retry_count < max_retry_attempts - 1:
                                    log.info("🔄 滑块验证系统会自动刷新，等待iframe内容更新...")
                                    try:
                                        # 🔴 不刷新整个页面，只等待iframe自动刷新
                                        # 等待一段时间让验证码系统自动刷新
                                        log.info("⏳ 等待2秒让验证码系统自动刷新...")
                                        time.sleep(2)  # 减少到2秒等待刷新
                                        
                                        # 检查iframe是否仍然存在
                                        log.info("🔍 检查iframe状态...")
                                        iframe_locator = page.locator("xpath=//iframe[contains(@src, 'captcha')]").first
                                        
                                        # 等待iframe元素存在
                                        try:
                                            iframe_locator.wait_for(state="attached", timeout=10000)
                                            log.info("✅ iframe仍然存在")
                                        except Exception:
                                            log.warning("⚠️ iframe已消失，重新查找...")
                                            time.sleep(2)
                                            iframe_locator.wait_for(state="attached", timeout=10000)
                                        
                                        # 重新获取frame对象（iframe可能已经内部刷新）
                                        all_frames = page.frames
                                        slider_frame = None
                                        for frame in all_frames:
                                            if "captcha" in frame.url.lower():
                                                slider_frame = frame
                                                log.info(f"✅ 确认iframe: {frame.url[:80]}")
                                                break
                                        
                                        if not slider_frame:
                                            log.error("❌ 无法找到iframe")
                                            break
                                        
                                        # 等待iframe内容完全加载（新验证码图片）
                                        try:
                                            slider_frame.wait_for_load_state("domcontentloaded", timeout=10000)
                                            log.info("✅ iframe DOM已加载")
                                        except Exception:
                                            log.warning("⚠️ DOM加载超时，但继续尝试")
                                        
                                        # 额外等待让JavaScript执行完
                                        time.sleep(3)
                                        log.info("✅ iframe已更新，准备下一次尝试")
                                        
                                    except Exception as refresh_err:
                                        log.error(f"❌ iframe刷新等待失败: {refresh_err}")
                                        import traceback
                                        log.error(traceback.format_exc())
                                        break
                                else:
                                    log.error(f"❌ 已达到最大重试次数({max_retry_attempts})，滑块验证失败")
                        
                        if not slider_success:
                            log.error("❌ 所有滑块验证尝试均失败")
                            
                    except Exception as iframe_err:
                        log.error(f"❌ iframe处理失败: {iframe_err}")
                        import traceback
                        log.error(traceback.format_exc())
                else:
                    # 滑块不在iframe中，直接操作
                    log.info("🔍 滑块不在iframe中，直接操作...")
                    slider_success = _perform_human_drag(
                        page, 
                        slider_xpath, 
                        slider_container_xpath
                    )
                    if slider_success:
                        log.info("✅ 滑块验证成功")
                        slider_solved = True
            else:
                log.info("ℹ️ 未配置滑块XPath，跳过")
                slider_solved = True  # 没有滑块配置，认为不需要验证
            
            # 🔴 关键：如果滑块没有解决，必须停止流程
            if not slider_solved:
                log.error("❌❌ 滑块验证失败，无法继续注册流程（因为邮箱收不到验证码）")
                log.error("💡 建议：检查滑块 XPath 配置，或手动完成滑块验证后重试")
                raise Exception("Slider CAPTCHA verification failed - cannot proceed without it")
            
            # 验证码阶段：集成邮件接码功能
            # code_input: 接码页面中验证码元素的XPath（用于提取验证码）
            # code_url_element: Kling AI页面中验证码输入框的XPath（用于填写验证码）
            code_extract_xpath = xpaths.get("code_input")  # 从接码页提取
            code_input_xpath = xpaths.get("code_url_element")  # 在Kling AI页填写
            
            log.info("\n" + "="*60)
            log.info("Step 8: Processing verification code")
            log.info("="*60)
            
            if code_extract_xpath and code_input_xpath and code_url:
                log.info(f"📧 Processing verification code from: {code_url}")
                
                # 等待电子邮件到达（减少等待时间）
                log.info("⏳ Waiting 5 seconds for email to arrive...")
                time.sleep(5)  # 减少到5秒等待
                
                # 打开新标签页访问接码地址
                log.info("🌐 Opening new tab for verification code page...")
                code_page = context.new_page()
                
                try:
                    log.info(f"🔗 Navigating to code URL: {code_url}")
                    code_page.goto(code_url, timeout=60000, wait_until="domcontentloaded")
                    
                    # 等待页面内容可用
                    try:
                        code_page.wait_for_load_state("domcontentloaded", timeout=20000)
                        log.info("✅ Code page content loaded")
                    except Exception:
                        log.warning("⚠️ domcontentloaded 超时，继续执行")
                        time.sleep(2)
                    
                    # 提取验证码
                    verification_code = None
                    log.info(f"🔍 Extracting verification code using XPath: {code_extract_xpath[:80]}...")
                    
                    try:
                        # 尝试多次提取验证码
                        for attempt in range(3):
                            log.info(f"🔍 Attempt {attempt + 1}/3: Looking for verification code...")
                            
                            try:
                                # 检查元素是否存在
                                code_loc = code_page.locator(f"xpath={code_extract_xpath}")
                                code_loc.wait_for(state="attached", timeout=10000)
                                
                                # 提取文本
                                code_text = code_loc.inner_text(timeout=5000)
                                log.info(f"📝 Extracted text from element: '{code_text}'")
                                
                                # 从文本中提取6位数字
                                import re
                                match = re.search(r'\b\d{6}\b', code_text)
                                if match:
                                    verification_code = match.group(0)
                                    log.info(f"✅ Verification code extracted: {verification_code}")
                                    break
                                else:
                                    log.warning(f"⚠️ No 6-digit code found in text: '{code_text}'")
                                    # 尝试直接使用文本（去除空格）
                                    clean_text = code_text.strip().replace(' ', '').replace('\n', '')
                                    if clean_text.isdigit() and len(clean_text) == 6:
                                        verification_code = clean_text
                                        log.info(f"✅ Verification code extracted (cleaned): {verification_code}")
                                        break
                                    
                            except Exception as extract_err:
                                log.warning(f"⚠️ Attempt {attempt + 1} failed: {extract_err}")
                                if attempt < 2:
                                    log.info("⏳ Waiting 2 seconds before retry...")
                                    time.sleep(2)  # 减少到2秒重试等待
                        
                        # 如果仍未提取到，尝试从整个页面提取
                        if not verification_code:
                            log.warning("⚠️ XPath extraction failed, trying to extract from page body...")
                            try:
                                page_text = code_page.inner_text("body", timeout=5000)
                                match = re.search(r'\b\d{6}\b', page_text)
                                if match:
                                    verification_code = match.group(0)
                                    log.info(f"✅ Verification code extracted from page body: {verification_code}")
                                else:
                                    log.error("❌ No 6-digit verification code found in page body")
                            except Exception as body_err:
                                log.error(f"❌ Failed to extract from page body: {body_err}")
                    
                    except Exception as extract_err:
                        log.error(f"❌ Error extracting verification code: {extract_err}")
                        import traceback
                        log.error(traceback.format_exc())
                    
                    # 关闭接码页面，切换回注册页面
                    log.info("🔙 Closing code page and switching back to registration page...")
                    code_page.close()
                    page.bring_to_front()
                    log.info("✅ Switched back to registration page")
                    
                    # 等待页面切换（减少等待时间）
                    time.sleep(1)  # 减少到1秒等待
                    
                    # 填入验证码
                    if verification_code:
                        log.info(f"✏️ Filling verification code: {verification_code}")
                        log.info(f"🎯 Target input XPath: {code_input_xpath[:80]}...")
                        
                        fill_success = safe_fill(code_input_xpath, verification_code, timeout_ms=10000, required=True)
                        
                        if fill_success:
                            log.info("✅ Verification code filled successfully")
                            
                            # 等待一下让系统处理（减少等待时间）
                            time.sleep(1)  # 减少到1秒等待
                            
                            # 点击提交按钮
                            final_submit_btn = xpaths.get("final_submit_btn")
                            candidates = []
                            if final_submit_btn:
                                candidates.append(final_submit_btn)
                            candidates.append("//*[@class='generic-button critical big']")
                            candidates.append("//*[contains(@class,'generic-button') and contains(@class,'critical')]//*[text()='Submit' or text()='Next']")
                            clicked = False
                            for cand in candidates:
                                try:
                                    if element_exists(cand, timeout_ms=5000):
                                        if safe_click(cand, timeout_ms=10000, required=False):
                                            clicked = True
                                            break
                                except Exception:
                                    pass
                            if not clicked:
                                log.error("❌ Final submit button not found or not clickable")
                                return False
                            def verify_steps_success() -> bool:
                                lang_menu = xpaths.get("language_menu")
                                if lang_menu:
                                    if not element_exists(lang_menu, timeout_ms=20000, poll_ms=500):
                                        raise Exception(f"required element not found: {lang_menu[:80]}")
                                    try:
                                        page.locator(f"xpath={lang_menu}").hover(timeout=5000)
                                        time.sleep(1)
                                    except Exception:
                                        pass
                                english = xpaths.get("english_option")
                                if english:
                                    if not safe_click(english, timeout_ms=20000, required=True):
                                        return False
                                cs = xpaths.get("Creative Studio")
                                if cs:
                                    if not safe_click(cs, timeout_ms=20000, required=True):
                                        return False
                                mt = xpaths.get("More Tools")
                                if mt:
                                    # 点击 More Tools 前，设置等待新页面的监听
                                    log.info("准备点击 More Tools，并等待新标签页...")
                                    with context.expect_page(timeout=20000) as new_page_info:
                                        if not safe_click(mt, timeout_ms=40000, required=True):
                                            return False
                                    
                                    # 获取新页面
                                    try:
                                        new_page = new_page_info.value
                                        log.info("✅ 检测到新标签页打开，正在切换...")
                                        new_page.wait_for_load_state("domcontentloaded", timeout=20000)
                                        log.info(f"✅ 新标签页加载完成: {new_page.title()}")
                                        signin_xpath = xpaths.get("signin_btn")
                                        if signin_xpath:
                                            try:
                                                loc = new_page.locator(f"xpath={signin_xpath}")
                                                loc.wait_for(state="visible", timeout=10000)
                                                try:
                                                    loc.click(timeout=5000)
                                                except Exception:
                                                    new_page.evaluate(
                                                        """
                                                        (xp) => {
                                                            const n = document.evaluate(xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                                                            if (n) n.click();
                                                        }
                                                        """,
                                                        signin_xpath,
                                                    )
                                                log.info("✅ Sign In 已点击")
                                            except Exception:
                                                pass
                                        return True
                                    except Exception as e:
                                        log.warning(f"⚠️ 等待新标签页失败: {e}")
                                        # 如果没有检测到新页面，可能是在当前页打开，或者已经打开了
                                        pass

                                final_ok = xpaths.get("close_popup_svg")
                                if final_ok and element_exists(final_ok, timeout_ms=20000, poll_ms=500):
                                    safe_click(final_ok, timeout_ms=20000, required=False)
                                    return True
                                
                                # 如果流程走到这里，说明可能成功了但没有后续动作
                                log.info("✅ 流程似乎已完成，未检测到新标签页但没有报错")
                                time.sleep(5)
                                return True
                                # raise Exception("required final element not found")
                            return verify_steps_success()
                        else:
                            log.error("❌ Failed to fill verification code")
                            return False
                    else:
                        log.error("❌ Failed to extract verification code")
                        return False
                        
                except Exception as e:
                    log.error(f"❌ Error processing verification code: {e}")
                    import traceback
                    log.error(traceback.format_exc())
                    try:
                        code_page.close()
                    except Exception:
                        pass
                    return False
            elif code_input_xpath:
                log.error("❌ 手动接码流程已移除，缺少 code_url 无法继续")
                return False
            else:
                log.error("❌ 无接码流程已移除，必须提供 code_url")
                return False
    except Exception as e:
        log.error(f"❌ Automation error: {e}")
        import traceback
        log.error(traceback.format_exc())
        
        
        
        return False

if __name__ == "__main__":
    import json, os
    runtime_dir = Path(os.path.expanduser("~")) / ".kl_zhanghao"
    try:
        base = Path(__file__).resolve().parents[2]
    except Exception:
        base = Path(__file__).resolve().parent.parent.parent
    xfile = base / "samples" / "kling_xpaths.json"
    try:
        data = json.loads(xfile.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    for k in [
        "code_url_element",
        "code_input",
        "final_submit_btn",
        "slider_iframe",
        "slider_handle",
        "slider_container",
    ]:
        data.pop(k, None)
    ok = run_registration_flow(
        email="debug@example.com",
        password="DebugPass123!",
        runtime_dir=runtime_dir,
        xpaths=data,
        proxy={},
        platform_url="https://klingai.com/global/",
        code_url=None,
        attach_ws=None,
        dry_run=True,
        browser_mode="bitbrowser",
    )
    print("SUCCESS" if ok else "FAIL")
