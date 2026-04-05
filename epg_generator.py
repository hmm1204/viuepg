import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import pytz
import time
import os
import re
import ssl
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
    '491609': 'CHC影迷影院',
    '491572': 'CHC家庭影院',
    '491571': 'CHC动作电影',
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

HONGKONG_TZ = pytz.timezone('Asia/Hong_Kong')
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

def parse_utc(time_str):
    """解析时间字符串为UTC时间"""
    if not time_str:
        return None
    
    # 移除可能的空格和特殊字符
    time_str = time_str.strip()
    
    # 尝试常见格式
    formats = [
        "%Y%m%d%H%M%S",          # 20250614203000
        "%Y%m%d%H%M",            # 202506142030
        "%Y-%m-%dT%H:%M:%S",     # 2025-06-14T20:30:00
        "%Y-%m-%d %H:%M:%S",     # 2025-06-14 20:30:00
        "%Y/%m/%d %H:%M:%S",     # 2025/06/14 20:30:00
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(time_str, fmt)
            return pytz.utc.localize(dt)
        except ValueError:
            continue
    
    # 处理带时区的时间
    if len(time_str) >= 15:
        try:
            if ' ' in time_str:
                # 格式: 20250614203000 +0800
                time_part, tz_part = time_str.split(' ', 1)
                dt = datetime.strptime(time_part, "%Y%m%d%H%M%S")
                if tz_part and (tz_part.startswith('+') or tz_part.startswith('-')):
                    # 解析时区偏移
                    sign = -1 if tz_part.startswith('-') else 1
                    hours = int(tz_part[1:3]) if len(tz_part) >= 3 else 0
                    minutes = int(tz_part[3:5]) if len(tz_part) >= 5 else 0
                    tz_offset = timedelta(hours=hours, minutes=minutes) * sign
                    dt = dt - tz_offset
                return pytz.utc.localize(dt)
        except Exception:
            pass
    
    print(f"无法解析时间格式: {time_str}")
    return None

def process_programmes(root, channel_id):
    """处理节目数据"""
    if not root:
        return []
    
    # 查找programme元素
    programmes = root.findall('.//programme')
    if not programmes:
        return []
    
    # 过滤并排序有效节目
    valid_programmes = []
    for prog in programmes:
        start_time = prog.get('start')
        if start_time and parse_utc(start_time):
            valid_programmes.append(prog)
    
    if not valid_programmes:
        return []
    
    # 按开始时间排序
    valid_programmes.sort(key=lambda x: parse_utc(x.get('start')))
    
    processed = []
    for idx, prog in enumerate(valid_programmes):
        start_utc = parse_utc(prog.get('start'))
        stop_utc = parse_utc(prog.get('stop'))
        
        # 转换为香港时间
        start_hk = start_utc.astimezone(HONGKONG_TZ)
        
        # 确定结束时间
        if stop_utc:
            stop_hk = stop_utc.astimezone(HONGKONG_TZ)
        elif idx < len(valid_programmes) - 1:
            next_start = parse_utc(valid_programmes[idx+1].get('start'))
            if next_start:
                stop_hk = next_start.astimezone(HONGKONG_TZ)
            else:
                stop_hk = start_hk + timedelta(minutes=30)
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
            text = child.text or ''
            
            # 清理文本内容
            if tag in ['title', 'desc', 'sub-title'] and text:
                # 移除HTML标签
                text = re.sub(r'<[^>]+>', '', text)
                # 转义XML特殊字符
                text = (text.replace('&', '&amp;')
                          .replace('<', '&lt;')
                          .replace('>', '&gt;')
                          .replace('"', '&quot;')
                          .replace("'", '&apos;'))
                
                # 创建新元素
                new_child = ET.SubElement(new_prog, tag)
                if child.attrib:
                    for key, value in child.attrib.items():
                        new_child.set(key, value)
                new_child.text = text
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
            # 尝试解析XML
            content_str = xml_data.decode('utf-8', errors='ignore')
            # 修复可能的XML格式问题
            content_str = content_str.replace('&', '&amp;').replace('\x00', '')
            root = ET.fromstring(content_str)
            
            # 处理节目数据
            programmes = process_programmes(root, channel_id)
            print(f"  找到节目数: {len(programmes)}")
            
            # 添加到tv元素
            for prog in programmes:
                tv.append(prog)
            
            total_programs += len(programmes)
            successful_channels += 1
            
        except ET.ParseError as e:
            print(f"  XML解析错误: {e}")
            failed_channels.append(channel_id)
        except Exception as e:
            print(f"  处理错误: {e}")
            failed_channels.append(channel_id)
    
    # 保存XML文件
    try:
        # 创建XML声明并格式化
        xml_str = ET.tostring(tv, encoding='utf-8', method='xml')
        xml_str = xml_str.decode('utf-8')
        
        # 添加XML声明和格式化
        xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
        xml_content += '<!DOCTYPE tv SYSTEM "xmltv.dtd">\n'
        xml_content += xml_str
        
        # 保存文件
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(xml_content)
        
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
        try:
            test_root = ET.fromstring(xml_content.encode('utf-8'))
            channels = test_root.findall('channel')
            programmes = test_root.findall('programme')
            print(f"验证: 包含 {len(channels)} 个channel元素，{len(programmes)} 个programme元素")
            
            # 检查每个channel元素是否有id属性
            for chan in channels:
                chan_id = chan.get('id')
                if not chan_id:
                    print(f"警告: 发现无ID的channel元素")
        except Exception as e:
            print(f"验证XML时出错: {e}")
            
    except Exception as e:
        print(f"保存文件失败: {e}")

if __name__ == '__main__':
    main()
