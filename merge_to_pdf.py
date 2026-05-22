import os
import img2pdf
from PIL import Image

# ==================== 配置 ====================
OUTPUT_PDF = "漫画.pdf"         # 输出的 PDF 文件名
USE_IMG2PDF = True               # True: 使用 img2pdf（推荐）; False: 使用 Pillow
# ================================================

def sorted_subdirs(root):
    """返回 root 下类似 '第001话' 的文件夹，按名称排序"""
    dirs = []
    for name in os.listdir(root):
        full = os.path.join(root, name)
        if os.path.isdir(full) and name.startswith("第") and name.endswith("话"):
            dirs.append(name)
    dirs.sort()
    return dirs

def sorted_image_files(chapter_dir):
    """返回章节文件夹内所有图片文件绝对路径，按文件名排序"""
    files = []
    for fname in os.listdir(chapter_dir):
        if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
            files.append(os.path.join(chapter_dir, fname))
    files.sort()
    return files

def merge_pdf_img2pdf(all_images, output_path):
    with open(output_path, "wb") as f:
        f.write(img2pdf.convert(all_images))

def merge_pdf_pillow(all_images, output_path):
    if not all_images:
        return
    images = []
    for path in all_images:
        img = Image.open(path)
        if img.mode == 'RGBA':
            img = img.convert('RGB')
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        images.append(img)
    if not images:
        return
    first = images[0]
    rest = images[1:]
    first.save(output_path, save_all=True, append_images=rest)

def main():
    base_dir = "."          # 脚本放在漫画根目录运行
    print(f"正在扫描目录: {os.path.abspath(base_dir)}")
    chapters = sorted_subdirs(base_dir)
    if not chapters:
        print("没有找到任何 '第XXX话' 的文件夹！请确认脚本放在了正确位置。")
        return

    all_images = []
    for chap in chapters:
        chap_path = os.path.join(base_dir, chap)
        images = sorted_image_files(chap_path)
        print(f"{chap}: 找到 {len(images)} 张图片")
        all_images.extend(images)

    if not all_images:
        print("没有找到任何图片！")
        return

    print(f"\n总计 {len(all_images)} 张图片，开始生成 PDF...")
    if USE_IMG2PDF:
        merge_pdf_img2pdf(all_images, OUTPUT_PDF)
    else:
        merge_pdf_pillow(all_images, OUTPUT_PDF)

    print(f"✅ 完成！PDF 已生成: {os.path.abspath(OUTPUT_PDF)}")

if __name__ == "__main__":
    main()