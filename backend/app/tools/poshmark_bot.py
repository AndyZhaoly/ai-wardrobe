"""
Poshmark 自动发布 Bot
使用 Playwright 自动化填写发布表单
"""
import os
import re
import time
import traceback
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# 浏览器数据保存目录（用于保持登录状态）
import os as _os
USER_DATA_DIR = _os.getenv("POSHMARK_BROWSER_DATA_PATH", "./poshmark_browser_data")


def ensure_user_data_dir():
    """确保浏览器数据目录存在"""
    Path(USER_DATA_DIR).mkdir(parents=True, exist_ok=True)


def create_poshmark_listing(
    image_path: str,
    title: str,
    description: str,
    original_price: str,
    listing_price: str,
    category_path: list = None,  # 例如：["Women", "Tops", "Blouses"]
    headless: bool = False,
    auto_submit: bool = False  # Demo 阶段建议 False，避免真的发帖
) -> dict:
    """
    自动在 Poshmark 创建 listing

    Args:
        image_path: 图片路径（支持相对路径或绝对路径）
        title: 商品标题
        description: 商品描述
        original_price: 原价
        listing_price: 售价
        category_path: 分类路径，如 ["Women", "Tops", "Blouses"]
        headless: 是否无头模式（Demo 建议 False）
        auto_submit: 是否自动点击发布（Demo 建议 False）

    Returns:
        {"success": bool, "message": str, "status": str}
    """
    ensure_user_data_dir()

    # 转换为绝对路径
    abs_image_path = os.path.abspath(image_path)
    if not os.path.exists(abs_image_path):
        return {
            "success": False,
            "message": f"图片不存在: {image_path}",
            "status": "error"
        }

    # 默认分类路径
    if category_path is None:
        category_path = ["Women", "Tops"]

    browser = None
    try:
        with sync_playwright() as p:
            print(f"[PoshmarkBot] 正在启动浏览器...")
            browser = p.chromium.launch_persistent_context(
                USER_DATA_DIR,
                headless=headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-web-security',
                ],
                viewport={"width": 1440, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

            page = browser.new_page()

            # 将浏览器窗口带到前台（macOS）
            try:
                import subprocess
                subprocess.run(["osascript", "-e", 'tell application "Chromium" to activate'], check=False, timeout=2)
            except:
                pass

            # 进入发帖页面
            print(f"[PoshmarkBot] 进入 Poshmark 发帖页面...")
            page.goto("https://poshmark.com/create-listing", timeout=120000)
            # 只等待页面基本加载，不等待所有网络请求（避免超时）
            try:
                page.wait_for_load_state("domcontentloaded", timeout=30000)
            except:
                pass  # 即使没完全加载也继续检查

            # 检查是否需要登录
            print(f"[PoshmarkBot] 当前URL: {page.url}")
            if page.url.startswith("https://poshmark.com/login") or "login" in page.url:
                print(f"[PoshmarkBot] ⚠️ 需要登录")
                if headless:
                    print(f"[PoshmarkBot] 无头模式无法手动登录，请先运行一次非无头模式完成登录")
                    browser.close()
                    return {
                        "success": False,
                        "message": "Poshmark 需要登录。请先运行 'python tools/poshmark_bot.py' 不带 headless 参数，在打开的浏览器中手动登录一次",
                        "status": "login_required"
                    }
                print(f"[PoshmarkBot] 请在浏览器中手动登录...")
                # 等待用户登录（最多等180秒，给足时间）
                print(f"[PoshmarkBot] 请在浏览器中完成登录（等待180秒）...")
                for i in range(180):
                    if "create-listing" in page.url:
                        print(f"[PoshmarkBot] 登录成功！")
                        break
                    time.sleep(1)
                    if i % 30 == 0 and i > 0:
                        print(f"[PoshmarkBot] 仍在等待登录... 已等待{i}秒，还剩{180-i}秒")
                else:
                    browser.close()
                    return {
                        "success": False,
                        "message": "登录超时（3分钟），请重新运行并尽快完成登录",
                        "status": "login_required"
                    }

            print(f"[PoshmarkBot] 已登录，开始填写表单...")

            # ============== 全局表单填写流程（带异常捕获）=============
            try:
                # 辅助函数：滚动到元素并点击
                def scroll_and_click(locator, timeout=10000):
                    locator.scroll_into_view_if_needed(timeout=timeout)
                    locator.click(timeout=timeout)

                def scroll_and_fill(locator, text, delay=50):
                    locator.scroll_into_view_if_needed()
                    locator.press_sequentially(text, delay=delay)

                # 1. 上传图片
                print(f"[PoshmarkBot] 上传图片: {os.path.basename(abs_image_path)}")
                try:
                    # 等待文件输入框出现
                    file_input = page.wait_for_selector('input[type="file"]', timeout=30000)
                    file_input.set_input_files(abs_image_path)
                    print(f"[PoshmarkBot] 图片已选择，等待上传...")
                    time.sleep(2)

                    # 点击 Apply/Done 按钮确认上传
                    print(f"[PoshmarkBot] 点击 Apply 确认上传...")
                    try:
                        # 尝试多种可能的按钮文本
                        for btn_text in ["Apply", "Done", "Upload", "Save"]:
                            try:
                                btn = page.get_by_role("button", name=btn_text).first
                                if btn.is_visible():
                                    btn.click()
                                    print(f"[PoshmarkBot] 点击了 {btn_text} 按钮")
                                    break
                            except:
                                continue
                    except Exception as e:
                        print(f"[PoshmarkBot] 点击确认按钮失败（可能不需要）: {e}")

                    # 等待图片预览出现
                    time.sleep(3)
                    print(f"[PoshmarkBot] 图片上传完成")
                except Exception as e:
                    print(f"[PoshmarkBot] 图片上传遇到问题: {e}，尝试继续...")
                    time.sleep(3)  # 即使出错也继续尝试

                # 2. 填写标题
                print(f"[PoshmarkBot] 填写标题...")
                title_input = page.get_by_placeholder("What are you selling?")
                title_input.scroll_into_view_if_needed()
                title_input.fill(title)
                time.sleep(0.5)

                # 3. 填写描述
                print(f"[PoshmarkBot] 填写描述...")
                desc_input = page.get_by_placeholder("Describe it!")
                desc_input.scroll_into_view_if_needed()
                desc_input.fill(description)
                time.sleep(0.5)

                # ==========================================
                # 🏷️ 核心动作 4：处理下拉菜单 (Category)
                # ==========================================
                # 选两级：Category + 子分类，不选第三级
                categories_to_select = category_path[:2] if len(category_path) >= 2 else category_path
                print(f"[PoshmarkBot] 选择分类: {' > '.join(categories_to_select)}")
                try:
                    # 1. 点击 Select Category 展开侧边栏 (使用准确选择器)
                    page.locator(".dropdown__selector.dropdown__selector--select-tag").first.click()
                    time.sleep(0.8)  # 等待侧边栏滑出

                    # 2. 逐级点击分类
                    # Poshmark 逻辑：点击主分类后进入子分类列表，需要点 "All Categories" 返回
                    for i, cat in enumerate(categories_to_select):
                        print(f"[PoshmarkBot]   点击: {cat}")
                        clicked = False

                        # 选子分类前，先返回主列表（如果不是第一个分类）
                        if i >= 1:
                            try:
                                all_cat = page.locator("a").filter(has_text=re.compile(r"All Categories")).first
                                if all_cat.is_visible(timeout=2000):
                                    print(f"[PoshmarkBot]   返回 All Categories...")
                                    all_cat.click()
                                    time.sleep(0.5)
                            except:
                                pass

                        # 方法1：直接点击
                        try:
                            if cat in ["Women", "Men", "Kids", "Home", "Pets", "Electronics"]:
                                # 主分类用 nth 定位更稳定
                                if cat == "Women":
                                    page.locator("a").nth(2).click()
                                elif cat == "Men":
                                    page.locator("a").nth(3).click()
                                else:
                                    page.get_by_text(cat, exact=False).first.click()
                            else:
                                # 子分类 - 先确保父分类已选中
                                parent_cat = categories_to_select[0]
                                print(f"[PoshmarkBot]   先进入父分类: {parent_cat}")
                                if parent_cat == "Women":
                                    page.locator("a").nth(2).click()
                                elif parent_cat == "Men":
                                    page.locator("a").nth(3).click()
                                else:
                                    page.get_by_text(parent_cat, exact=False).first.click()
                                time.sleep(0.5)

                                # 现在点击子分类
                                print(f"[PoshmarkBot]   点击子分类: {cat}")
                                page.get_by_text(cat).click()
                            clicked = True
                            print(f"[PoshmarkBot]   ✓ 点击成功: {cat}")
                        except Exception as e:
                            print(f"[PoshmarkBot]   方法1失败: {e}")

                        # 方法2：强制点击
                        if not clicked:
                            try:
                                page.get_by_text(cat).click(force=True)
                                clicked = True
                                print(f"[PoshmarkBot]   ✓ 强制点击成功: {cat}")
                            except Exception as e2:
                                print(f"[PoshmarkBot]   ⚠️ 无法点击: {cat}")

                        time.sleep(0.3)

                    # 关闭侧边栏（多种方式确保关闭）
                    print("[PoshmarkBot] 关闭分类选择器...")
                    page.keyboard.press("Escape")
                    time.sleep(0.5)
                    page.mouse.click(10, 10)
                    time.sleep(0.3)

                    if len(category_path) > 2:
                        print(f"[PoshmarkBot] 💡 已选 {' > '.join(categories_to_select)}，更细分类请主人手动选择")

                except Exception as e:
                    print(f"[PoshmarkBot] ⚠️ 分类选择失败: {e}")

                # ==========================================
                # 📏 步骤1：尺码选择 (Custom OS 增强版)
                # ==========================================
                try:
                    print("[PoshmarkBot] 正在处理 Custom Size...")
                    # 确保分类选择器已关闭
                    time.sleep(0.5)
                    # 使用准确的 data-test 选择器
                    page.locator("[data-test='size']").click()
                    time.sleep(0.8)
                    # Custom 标签页 - 动态检测所有 tabs，从最后一个开始
                    clicked = False
                    try:
                        # 找到所有 horizontal-nav 元素
                        all_tabs = page.locator("[data-test^='horizontal-nav']")
                        tab_count = all_tabs.count()
                        print(f"[PoshmarkBot]   找到 {tab_count} 个 tabs，从最后一个开始找")

                        for i in range(tab_count - 1, -1, -1):  # 倒序遍历
                            try:
                                tab = all_tabs.nth(i)
                                text = tab.inner_text(timeout=1000)
                                print(f"[PoshmarkBot]     tab {i}: {text}")
                                if "Custom" in text:
                                    tab.click()
                                    print(f"[PoshmarkBot]   ✓ 点击 tab {i} (Custom)")
                                    clicked = True
                                    break
                            except:
                                continue
                    except Exception as e:
                        print(f"[PoshmarkBot]   动态检测失败: {e}")

                    if not clicked:
                        # 回退：直接点文字
                        page.get_by_text("Custom").click()
                        print("[PoshmarkBot]   ✓ 点击 Custom 文字")
                    time.sleep(0.5)

                    # 点击输入框并输入 OS（原始 codegen 版本）
                    print("[PoshmarkBot] 点击输入框...")
                    page.locator("#customSizeInput0").click()
                    time.sleep(0.3)
                    page.locator("#customSizeInput0").fill("OS")
                    time.sleep(0.3)

                    # 点击 Save
                    page.get_by_role("button", name="Save").click()
                    time.sleep(0.5)

                    # 点击 Done
                    page.get_by_role("button", name="Done").click()
                    print("[PoshmarkBot] Size 设置完成")

                except Exception as e:
                    print(f"[PoshmarkBot] Custom 流程异常: {e}")
                    # 出错时也强制关闭弹窗
                    try:
                        page.keyboard.press("Escape")
                        page.mouse.click(10, 10)
                    except:
                        pass

                # ==========================================
                # ✨ 步骤2：填写 CONDITION
                # ==========================================
                try:
                    print("[PoshmarkBot] 选择成色...")
                    # 使用准确的选择器
                    page.locator(".listing-editor__condition-container > div > .dropdown > div > .dropdown__selector").click()
                    time.sleep(0.5)
                    # 选择 "New without tags or like new" 或备选 "Good"
                    try:
                        page.get_by_text("New without tags or like new").click()
                    except:
                        page.get_by_text("Good").click()

                except Exception as e:
                    print(f"[PoshmarkBot] ⚠️ 成色填写遇到小问题，已跳过: {e}")

                # ==========================================
                # ✨ 步骤3：填写 BRAND
                # ==========================================
                try:
                    # 使用标题中的品牌或默认 Vintage
                    brand = "Vintage"
                    if title:
                        # 尝试从标题提取品牌（第一个单词通常是品牌）
                        first_word = title.split()[0] if title.split() else ""
                        if first_word and first_word not in ["Vintage", "Beautiful", "Great"]:
                            brand = first_word

                    # 填写品牌 (输入框)
                    print("[PoshmarkBot] 填写品牌...")
                    brand_input = page.get_by_placeholder("Enter the Brand/Designer")
                    brand_input.fill(brand)
                    time.sleep(0.3)
                    # 尝试快速选一个联想菜单项（不等它完全加载）
                    try:
                        page.locator(".suggested-brands-container .dropdown-item").first.click(timeout=500)
                    except:
                        pass # 如果没有联想菜单也没关系

                except Exception as e:
                    print(f"[PoshmarkBot] ⚠️ 品牌填写遇到小问题，已跳过: {e}")

                # ==========================================
                # 💰 步骤4：价格输入 (使用录制代码的准确步骤)
                # ==========================================
                try:
                    print("[PoshmarkBot] 正在定位价格区域...")
                    # 滚动到 Price 区域
                    try:
                        page.get_by_text("Listing Price").first.scroll_into_view_if_needed()
                    except:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.7)")
                    time.sleep(0.5)

                    # 使用录制的准确步骤
                    try:
                        # 1. 点击 spinbutton 激活价格区域
                        page.get_by_role("spinbutton", name="form__text").click()
                        time.sleep(0.3)
                        # 2. 填写 Listing Price
                        page.get_by_placeholder("*Required").nth(2).fill(str(listing_price))
                        time.sleep(0.3)
                        # 3. 点击第二个 spinbutton 激活 Original Price
                        page.get_by_role("spinbutton").nth(2).click()
                        time.sleep(0.3)
                        # 4. 填写 Original Price
                        page.get_by_role("spinbutton").nth(2).fill(str(original_price))
                        time.sleep(0.3)
                        # 5. 点击 Done
                        page.get_by_role("button", name="Done").click()
                        print("[PoshmarkBot] 价格填写完成")
                    except Exception as e:
                        print(f"[PoshmarkBot] 价格填写失败，尝试最后保底手段: {e}")
                        # 保底：使用 JavaScript 直接设置值
                        try:
                            page.evaluate(f"""
                                const inputs = document.querySelectorAll('input[placeholder="*Required"]');
                                if (inputs.length >= 3) {{
                                    inputs[2].value = '{listing_price}';
                                    inputs[2].dispatchEvent(new Event('input', {{ bubbles: true }}));
                                }}
                                const spinInputs = document.querySelectorAll('input[type="number"]');
                                if (spinInputs.length >= 3) {{
                                    spinInputs[2].value = '{original_price}';
                                    spinInputs[2].dispatchEvent(new Event('input', {{ bubbles: true }}));
                                }}
                            """)
                            print("[PoshmarkBot] 价格已通过 JS 设置")
                        except:
                            pass

                except Exception as e:
                    print(f"[PoshmarkBot] 价格区域定位失败: {e}")

                print(f"[PoshmarkBot] ✅ 表单填写完成！")

                # 7. 是否自动提交（Demo 阶段建议不提交）
                if auto_submit:
                    print(f"[PoshmarkBot] 正在提交发布...")
                    # 找到 Next / List This Item 按钮
                    try:
                        next_btn = page.get_by_role("button", name="Next")
                        if next_btn.is_visible():
                            next_btn.click()
                            time.sleep(2)
                    except:
                        pass

                    try:
                        list_btn = page.get_by_role("button", name="List This Item")
                        if list_btn.is_visible():
                            list_btn.click()
                            time.sleep(3)
                    except:
                        pass

                    status = "published"
                    message = "商品已成功发布到 Poshmark！"
                else:
                    status = "form_filled"
                    message = "表单已自动填好，请主人确认后点击发布～"
                    print(f"[PoshmarkBot] ⏸️ 已暂停，等待手动确认（Demo 模式）")

                # 保持浏览器打开一会儿，让用户看到结果
                if not headless:
                    time.sleep(3)

                browser.close()
                browser = None

                return {
                    "success": True,
                    "message": message,
                    "status": status,
                    "data": {
                        "title": title,
                        "listing_price": listing_price,
                        "original_price": original_price,
                        "image": abs_image_path
                    }
                }

            except Exception as form_error:
                # 表单填写过程中的任何错误都在这里捕获
                error_msg = f"表单填写失败: {str(form_error)}"
                print(f"[PoshmarkBot] ❌ {error_msg}")
                print(f"[PoshmarkBot] 错误详情:\n{traceback.format_exc()}")

                # 截图保存用于调试
                try:
                    if page:
                        screenshot_path = "./poshmark_error_screenshot.png"
                        page.screenshot(path=screenshot_path)
                        print(f"[PoshmarkBot] 错误截图已保存: {screenshot_path}")
                except:
                    pass

                raise form_error

    except Exception as e:
        error_msg = f"发布失败: {str(e)}"
        print(f"[PoshmarkBot] ❌ {error_msg}")
        return {
            "success": False,
            "message": error_msg,
            "status": "form_error"
        }

    finally:
        # 确保浏览器被关闭
        if browser:
            try:
                browser.close()
                print("[PoshmarkBot] 浏览器已关闭")
            except:
                pass


def auto_publish_from_gemini_result(
    image_path: str,
    gemini_result: dict,
    headless: bool = False,
    auto_submit: bool = False,
    custom_description: str = None
) -> dict:
    """
    根据 Gemini 分析结果自动发布到 Poshmark

    Args:
        image_path: 衣物图片路径
        gemini_result: Gemini 分析结果
        headless: 是否无头模式
        auto_submit: 是否自动提交
        custom_description: 可选，自定义描述文案（如 Agent 生成的文案）

    Returns:
        发布结果
    """
    # 提取信息
    item = gemini_result.get("item_details", {})
    official = gemini_result.get("official_price", {})
    resale = gemini_result.get("resale_estimate", {})

    brand = item.get("brand", "Unknown")
    model = item.get("model_name", "Fashion Item")
    category = item.get("category", "Tops")
    condition = item.get("condition", "Good")

    # 构建标题
    if brand and brand != "Unknown":
        title = f"{brand} {model}"[:50]  # Poshmark 标题限制
    else:
        title = f"Vintage {category}"[:50]

    # 构建描述 - 优先使用自定义文案（如 Agent 生成的）
    if custom_description:
        description = custom_description
        print(f"[PoshmarkBot] 使用 Agent 生成的自定义文案")
    else:
        # 构建默认描述（加入尺码免责声明）
        description_parts = [
            f"Beautiful {brand} {model}. {condition} condition.",
            "",
            f"Original Price: ${official.get('amount', 'N/A')}",
            "",
            f"Material: {item.get('material', 'Unknown')}",
            f"Color: {item.get('color', 'Mixed')}",
            "",
            "📏 Size: OS (One Size). Size tag is missing/unclear. Listed as OS.",
            "Please refer to measurements or photos before purchasing.",
            "",
            "Ships from US. Open to reasonable offers!",
            "",
            f"Tags: #{brand.replace(' ', '')} #vintage #fashion #{category.replace(' ', '')} #onesize"
        ]
        description = "\n".join(description_parts)

    # 价格（Poshmark 用 USD）
    CNY_TO_USD = 0.14  # 约 1 CNY = 0.14 USD

    def to_usd(amount, currency: str) -> int:
        if not amount:
            return 0
        currency = (currency or "").upper()
        if currency in ("CNY", "RMB", "¥", "元"):
            return max(1, round(amount * CNY_TO_USD))
        return int(amount)

    original_price = to_usd(official.get("amount", 0), official.get("currency", "USD")) or 100
    listing_price = to_usd(resale.get("max_price", 0), resale.get("currency", "CNY")) or 50

    # 确定分类路径 - 优先使用 Gemini 返回的 poshmark_category
    category_path = ["Women", "Tops"]  # 默认
    poshmark_cat = gemini_result.get("poshmark_category", {})
    if poshmark_cat and poshmark_cat.get("department") and poshmark_cat.get("category"):
        category_path = [poshmark_cat["department"], poshmark_cat["category"]]
        print(f"[PoshmarkBot] Gemini 推荐分类: {' > '.join(category_path)} (置信度: {poshmark_cat.get('confidence', '未知')})")
    else:
        # 回退到旧的中文关键词匹配
        category_mapping = {
            "上衣": ["Women", "Tops"],
            "衬衫": ["Women", "Tops", "Blouses"],
            "T恤": ["Women", "Tops", "Tees"],
            "外套": ["Women", "Jackets"],
            "夹克": ["Women", "Jackets"],
            "连衣裙": ["Women", "Dresses"],
            "裙子": ["Women", "Skirts"],
            "裤子": ["Women", "Pants"],
            "下装": ["Women", "Pants"],
            "鞋": ["Women", "Shoes"],
            "包": ["Women", "Bags"],
        }
        for key, path in category_mapping.items():
            if key in category:
                category_path = path
                break

    print(f"[PoshmarkBot] 准备发布: {title}")
    print(f"[PoshmarkBot] 分类: {' > '.join(category_path)}")

    return create_poshmark_listing(
        image_path=image_path,
        title=title,
        description=description,
        original_price=str(original_price),
        listing_price=str(listing_price),
        category_path=category_path,
        headless=headless,
        auto_submit=auto_submit
    )


if __name__ == "__main__":
    # 测试运行
    import sys

    if len(sys.argv) > 1:
        test_image = sys.argv[1]
    else:
        # 查找示例图片
        test_files = [
            "./extracted_clothes/upper_0.png",
            "./images/white_shirt.jpg",
            "./images/denim_jacket.jpg"
        ]
        test_image = None
        for f in test_files:
            if os.path.exists(f):
                test_image = f
                break

        if not test_image:
            print("用法: python poshmark_bot.py <image_path>")
            print("或放置图片到 ./extracted_clothes/ 目录")
            sys.exit(1)

    print(f"\n测试发布图片: {test_image}\n")

    result = create_poshmark_listing(
        image_path=test_image,
        title="Vintage Blue Denim Jacket",
        description="Beautiful 90s vintage denim jacket. Great condition!\n\n#vintage #denim #fashion",
        original_price="150",
        listing_price="45",
        category_path=["Women", "Jackets"],
        headless=False,
        auto_submit=False  # Demo 模式，不真发布
    )

    print(f"\n结果: {result['message']}")
    print(f"状态: {result['status']}")
