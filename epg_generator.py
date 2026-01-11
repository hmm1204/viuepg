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

# 配置部分
CHANNEL_IDS = [
    '400477',  # TNT Sports 1 HD
    '400480',  # TNT Sports 2 HD
    '400479',  # TNT Sports 3 HD
    '400478',  # TNT Sports 4 HD
    '491609',  # CHC影迷影院
    '491572',  # CHC家庭影院
    '457802',  # CHC动作电影
    '410286',  # Now爆谷星影台
    '410285',  # Now爆谷台
    '369671',  # iQIYI HD
    '1298',    # 天映频道
    '368550',  # 港台电视31
    '368551',  # 港台电视32
    '410274',  # ViuTV
    '410273'   # ViuTVsix
]
HONGKONG_TZ = pytz.timezone('Asia/Hong_Kong')
BASE_URL = "https://epg.pw/api/epg.xml?channel_id={channel_id}"

# GitHub Actions 适配
OUTPUT_DIR = os.getcwd()
OUTPUT_FILE = os.path.join(OUTPUT_DIR, 'viu.xml')

# 自定义SSL上下文
def create_ssl_context():
    ssl_context = ssl.create_default_context()
    ssl_context.options |= ssl.OP_NO_SSLv2
    ssl_context.options |= ssl.OP_NO_SSLv3
    ssl_context.set_ciphers('DEFAULT')
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return ssl_context

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
    print(f"获取频道 {channel_id} 的EPG数据: {url}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Accept': 'application/xml, text/xml, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Connection': 'close'
    }
    
    for attempt in range(3):
        try:
            response = session.get(url, headers=headers, timeout=30, verify=False)
            if response.status_code == 200:
                # 确认内容是XML
                content_type = response.headers.get('Content-Type', '')
                if 'xml' not in content_type:
                    print(f"警告: 响应内容可能不是XML格式 ({content_type})")
                return response.content
            else:
                print(f"请求失败，状态码: {response.status_code}")
                if response.status_code == 403:
                    print("收到403 Forbidden错误，尝试使用备用User-Agent")
                    # 尝试备用User-Agent
                    headers['User-Agent'] = 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
        except Exception as e:
            print(f"尝试 {attempt+1}/3 出错: {str(e)}")
        time.sleep(2)
    return None

def parse_utc(time_str):
    if not time_str:
        return None
        
    # 尝试解析常见格式
    formats = [
        "%Y%m%d%H%M%S",    # 20250614203000
        "%Y%m%d%H%M",       # 202506142030
        "%Y-%m-%dT%H:%M:%S", # 2025-06-14T20:30:00
        "%Y-%m-%d %H:%M:%S", # 2025-06-14 20:30:00
        "%Y/%m/%d %H:%M:%S", # 2025/06/14 20:30:00
        "%Y%m%d",            # 20250614
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(time_str, fmt)
            return pytz.utc.localize(dt)
        except ValueError:
            continue
            
    # 处理带时区的时间
    if ' ' in time_str and len(time_str) > 15:
        try:
            time_part, tz_part = time_str.split(' ', 1)
            dt = datetime.strptime(time_part, "%Y%m%d%H%M%S")
            if tz_part.startswith('+') or tz_part.startswith('-'):
                sign = -1 if tz_part.startswith('-') else 1
                hours = int(tz_part[1:3])
                minutes = int(tz_part[3:5])
                tz_offset = timedelta(hours=hours, minutes=minutes) * sign
                dt = dt - tz_offset
            return pytz.utc.localize(dt)
        except Exception:
            pass
            
    if time_str.isdigit():
        try:
            return datetime.fromtimestamp(int(time_str), pytz.utc)
        except Exception:
            pass
            
    print(f"无法解析时间格式: {time_str}")
    return None

def process_programmes(programmes, channel_id):
    if not programmes:
        return []
        
    valid_programmes = []
    for prog in programmes:
        if parse_utc(prog.get('start')):
            valid_programmes.append(prog)
        else:
            title = prog.findtext('title') or '未知节目'
            print(f"跳过无效时间格式的节目: {title}")
    
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
        
        # 确保结束时间有效
        if not stop_hk or stop_hk <= start_hk:
            stop_hk = start_hk + timedelta(minutes=30)
            
        # 创建新节目元素
        new_prog = ET.Element('programme')
        new_prog.set('start', start_hk.strftime("%Y%m%d%H%M%S +0800"))
        new_prog.set('stop', stop_hk.strftime("%Y%m%d%H%M%S +0800"))
        new_prog.set('channel', channel_id)
        
        # 复制子元素并清理
        for child in prog:
            if child.tag == 'desc' and child.text:
                # 清理HTML标签和非法字符
                text = re.sub(r'<[^>]+>', '', child.text)
                text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                child.text = text
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
    
    if 'GITHUB_ACTIONS' in os.environ:
        print("运行环境: GitHub Actions")
    print("=" * 50)
    
    # 创建XML根元素
    tv = ET.Element('tv')
    tv.set('generator-info-name', 'EPG Generator')
    tv.set('generator-info-url', 'https://github.com/yourname/reponame')
    tv.set('date', datetime.now().strftime('%Y%m%d%H%M%S'))
    
    session = create_session()
    successful_channels = 0
    total_programs = 0
    
    for channel_id in CHANNEL_IDS:
        print(f"\n处理频道: {channel_id}")
        xml_data = fetch_xml(session, channel_id)
        
        if not xml_data:
            print(f"获取失败，跳过频道 {channel_id}")
            continue
        
        try:
            # 尝试解析XML
            root = ET.fromstring(xml_data)
            channel_elem = root.find('channel')
            if not channel_elem:
                print("缺少频道信息")
                continue
                
            channel_elem.set('id', channel_id)
            tv.append(channel_elem)
            
            programmes = root.findall('programme')
            if not programmes:
                print("无节目数据")
                continue
                
            print(f"找到 {len(programmes)} 个原始节目")
            processed = process_programmes(programmes, channel_id)
            print(f"处理后节目数: {len(processed)}")
            
            for prog in processed:
                tv.append(prog)
                
            successful_channels += 1
            total_programs += len(processed)
            
        except ET.ParseError:
            print("XML解析错误，尝试修复...")
            try:
                # 修复XML错误
                fixed_xml = xml_data.decode('utf-8', errors='ignore').replace('&', '&amp;')
                root = ET.fromstring(fixed_xml)
                
                # 重新处理...
                channel_elem = root.find('channel')
                if channel_elem:
                    channel_elem.set('id', channel_id)
                    tv.append(channel_elem)
                
                programmes = root.findall('programme')
                if programmes:
                    processed = process_programmes(programmes, channel_id)
                    for prog in processed:
                        tv.append(prog)
                    successful_channels += 1
                    total_programs += len(processed)
                    print(f"修复后节目数: {len(processed)}")
                else:
                    print("修复后仍未找到节目数据")
            except Exception as e:
                print(f"修复失败: {e}")
        except Exception as e:
            print(f"处理频道 {channel_id} 时出错: {e}")
    
    # 保存XML文件
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        
        # 创建XML声明并写入文件
        with open(OUTPUT_FILE, 'wb') as f:
            f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
            tree = ET.ElementTree(tv)
            tree.write(f, encoding='utf-8', xml_declaration=False)
        
        # 获取文件状态
        file_size = os.path.getsize(OUTPUT_FILE)
        print(f"\n成功保存EPG文件: {OUTPUT_FILE}")
        print(f"文件大小: {file_size/1024:.1f} KB")
        print(f"成功频道: {successful_channels}/{len(CHANNEL_IDS)}")
        print(f"总节目数: {total_programs}")
        
    except Exception as e:
        print(f"保存文件失败: {e}")
    
    duration = time.time() - start_time
    print(f"处理完成! 总耗时: {duration:.2f}秒")
    print("=" * 50)

if __name__ == '__main__':
    main()


