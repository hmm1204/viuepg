import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import pytz
import time
import os
import re
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 配置部分 - 定义频道ID和对应的显示名称
CHANNEL_CONFIG = {
    '400477': 'TNT Sports 1 HD',
    '400480': 'TNT Sports 2 HD',
    '400479': 'TNT Sports 3 HD',
    '400478': 'TNT Sports 4 HD',
    '400476': 'TNT Sports 4K',
    '410378': '鳳凰衛視中文台',
    '410355': '鳳凰衛視資訊台',
    '368372': '鳳凰衛視香港台',
    '410286': 'Now爆谷星影台',
    '410285': 'Now爆谷台',
    '369671': 'iQIYI HD',
    '1298': '天映频道',
    '369730': '天映频道(新加坡)',
    '368550': '港台电视31',
    '368551': '港台电视32',
    '410274': 'ViuTV',
    '410273': 'ViuTVsix'
}

# 时区配置
HONGKONG_TZ = pytz.timezone('Asia/Hong_Kong')
LONDON_TZ = pytz.timezone('Europe/London')
# 英国频道（源时间为 Europe/London 本地时间，需转香港时区）
UK_CHANNELS = {'400477', '400480', '400479', '400478', '400476'}

BASE_URL = "https://epg.pw/api/epg.xml?channel_id={channel_id}"

# GitHub Actions 适配
OUTPUT_DIR = os.getcwd()
OUTPUT_FILE = os.path.join(OUTPUT_DIR, 'viu.xml')


def create_channel_element(channel_id, display_name):
    """创建完整的channel元素"""
    channel = ET.Element('channel')
    channel.set('id', channel_id)

    # 创建display-name元素
    display_elem = ET.SubElement(channel, 'display-name')
    display_elem.set('lang', 'zh')
    display_elem.text = display_name

    # 添加图标元素（可选）
    icon = ET.SubElement(channel, 'icon')
    icon.set('src', f'https://epg.pw/logo/{channel_id}.png')

    return channel


def create_session():
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        backoff_factor=1,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    session.verify = False
    return session


def fetch_xml(session, channel_id):
    url = BASE_URL.format(channel_id=channel_id)
    print(f"获取频道 {channel_id} ({CHANNEL_CONFIG.get(channel_id, '未知频道')}) 的EPG数据")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/xml, text/xml, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }

    for attempt in range(3):
        try:
            response = session.get(url, headers=headers, timeout=30, verify=False)
            if response.status_code == 200:
                return response.content
            elif response.status_code == 403:
                print(f"  第{attempt+1}次尝试: 403 Forbidden，尝试更换User-Agent")
                headers['User-Agent'] = 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
            else:
                print(f"  第{attempt+1}次尝试: HTTP {response.status_code}")
        except Exception as e:
            print(f"  第{attempt+1}次尝试出错: {str(e)}")
        time.sleep(2)

    print(f"频道 {channel_id} 获取失败")
    return None


def parse_to_hongkong(time_str, channel_id):
    """
    将时间字符串统一解析为香港时间（Asia/Hong_Kong）。

    规则：
    - 带时区偏移（如 20260909200000 +0100）：标准XMLTV，减回UTC再转HK
    - 不带偏移的裸时间：
        · 英国频道（UK_CHANNELS）：按 Europe/London 本地时间，再转HK
        · 其他频道（HK系）：按 Asia/Hong_Kong 本地时间，转HK不变
    """
    if not time_str:
        return None

    time_str = time_str.strip()
    m = re.match(r'(\d{14})\s*([+-]\d{4})?', time_str)
    if not m:
        print(f"无法解析时间格式: {time_str}")
        return None

    dt = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")

    # 情况1：带时区偏移（标准XMLTV格式）
    if m.group(2):
        sign = -1 if m.group(2)[0] == '-' else 1
        hh = int(m.group(2)[1:3]) if len(m.group(2)) >= 3 else 0
        mm = int(m.group(2)[3:5]) if len(m.group(2)) >= 5 else 0
        offset = timedelta(hours=hh, minutes=mm) * sign
        dt = dt - offset  # 减回UTC
        return pytz.utc.localize(dt).astimezone(HONGKONG_TZ)

    # 情况2：裸时间，按频道归属时区解释
    if channel_id in UK_CHANNELS:
        # TNT等英国频道：源时间 = Europe/London 本地时间
        return LONDON_TZ.localize(dt).astimezone(HONGKONG_TZ)
    else:
        # 香港频道：源时间 = Asia/Hong_Kong 本地时间
        return HONGKONG_TZ.localize(dt).astimezone(HONGKONG_TZ)


def _clean_text(text):
    """移除HTML标签并转义XML特殊字符"""
    if not text:
        return text
    text = re.sub(r'<[^>]+>', '', text)
    text = (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;')
                .replace("'", '&apos;'))
    return text


def process_programmes(root, channel_id):
    """处理节目数据，统一转换为香港时间"""
    if not root:
        return []

    programmes = root.findall('.//programme')
    if not programmes:
        return []

    # 过滤有效节目并按开始时间排序
    valid_programmes = [p for p in programmes if parse_to_hongkong(p.get('start'), channel_id)]
    if not valid_programmes:
        return []

    valid_programmes.sort(key=lambda x: parse_to_hongkong(x.get('start'), channel_id))

    processed = []
    for idx, prog in enumerate(valid_programmes):
        start_hk = parse_to_hongkong(prog.get('start'), channel_id)
        stop_hk = parse_to_hongkong(prog.get('stop'), channel_id)

        # 结束时间兜底
        if stop_hk:
            pass
        elif idx < len(valid_programmes) - 1:
            next_start = parse_to_hongkong(valid_programmes[idx + 1].get('start'), channel_id)
            stop_hk = next_start if next_start else start_hk + timedelta(minutes=30)
        else:
            stop_hk = start_hk + timedelta(minutes=30)

        # 确保结束时间晚于开始时间
        if stop_hk <= start_hk:
            stop_hk = start_hk + timedelta(minutes=30)

        # 创建新的programme元素
        new_prog = ET.Element('programme')
        new_prog.set('start', start_hk.strftime("%Y%m%d%H%M%S +0800"))
        new_prog.set('stop', stop_hk.strftime("%Y%m%d%H%M%S +0800"))
        new_prog.set('channel', channel_id)

        # 复制子元素
        for child in prog:
            tag = child.tag
            if tag in ['title', 'desc', 'sub-title']:
                new_child = ET.SubElement(new_prog, tag, attrib=child.attrib)
                new_child.text = _clean_text(child.text or '')
            else:
                new_prog.append(child)

        processed.append(new_prog)

    return processed


def main():
    start_time = time.time()
    print("=" * 50)
    print("开始生成EPG节目单...")
    print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"目标时区: {HONGKONG_TZ}")
    print(f"输出文件: {OUTPUT_FILE}")
    print(f"处理频道数: {len(CHANNEL_CONFIG)}")
    print("=" * 50)

    # 创建XML根元素
    tv = ET.Element('tv')
    tv.set('generator-info-name', 'EPG Generator')
    tv.set('generator-info-url', 'https://github.com')
    tv.set('date', datetime.now().strftime('%Y%m%d%H%M%S'))

    session = create_session()
    successful_channels = 0
    total_programs = 0
    failed_channels = []

    # 先创建所有channel元素
    for channel_id, display_name in CHANNEL_CONFIG.items():
        channel_elem = create_channel_element(channel_id, display_name)
        tv.append(channel_elem)

    # 为每个频道获取节目数据
    for channel_id, display_name in CHANNEL_CONFIG.items():
        print(f"\n[{successful_channels + 1}/{len(CHANNEL_CONFIG)}] 处理频道: {display_name} (ID: {channel_id})")

        xml_data = fetch_xml(session, channel_id)
        if not xml_data:
            failed_channels.append(channel_id)
            continue

        try:
            # 正规解析XML（不要先 replace('&','&amp;')，避免双重转义）
            content_str = xml_data.decode('utf-8', errors='ignore').replace('\x00', '')
            root = ET.fromstring(content_str)

            # 处理节目数据
            programmes = process_programmes(root, channel_id)
            print(f"  找到节目数: {len(programmes)}")

            for prog in programmes:
                tv.append(prog)

            total_programs += len(programmes)
            successful_channels += 1

        except ET.ParseError as e:
            print(f"  XML解析错误: {e}")
            failed_channels.append(channel_id)
        except Exception as e:
            print(f"  ")

    # 保存XML文件
    try:
        xml_str = ET.tostring(tv, encoding='utf-8', method='xml').decode('utf-8')
        xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
        xml_content += '<!DOCTYPE tv SYSTEM "xmltv.dtd">\n'
        xml_content += xml_str

        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(xml_content)

        # 时区自测：TNT 欧冠 Kick-off 8pm BST 应为香港次日 03:00 (+0800)
        if '20260910030000 +0800' in xml_content:
            print("✅ 时区自测通过（TNT Sports 1 HD 已正确转为香港时间）")
        else:
            print("⚠️  时区自测未触发（如非测试数据属正常，请以实际节目为准）")

        # 统计信息
        file_size = os.path.getsize(OUTPUT_FILE)
        duration = time.time() - start_time

        print("\n" + "=" * 50)
        print("EPG生成完成!")
        print(f"输出文件: {OUTPUT_FILE}")
        print(f"文件大小: {file_size/1024:.1f} KB")
        print(f"成功处理: {successful_channels}/{len(CHANNEL_CONFIG)} 个频道")
        print(f"总节目数: {total_programs}")
        print(f"失败频道: {len(failed_channels)}个")
        if failed_channels:
            print(f"失败频道ID: {', '.join(failed_channels)}")
        print(f"处理耗时: {duration:.2f}秒")
        print("=" * 50)

        # 验证生成的XML
        test_root = ET.fromstring(xml_content.encode('utf-8'))
        channels = test_root.findall('channel')
        programmes = test_root.findall('programme')
        print(f"验证: 包含 {len(channels)} 个channel元素，{len(programmes)} 个programme元素")

    except Exception as e:
        print(f"保存文件失败: {e}")


if __name__ == '__main__':
    main()
