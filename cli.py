import datetime
import time
import requests
import json
import os
import logging
import sys
import inquirer
import ntplib
from typing import Dict, List, Optional, Tuple
import qrcode_terminal
import urllib.parse
import hashlib
import qrcode
from PIL import Image
import threading
import io

VERSION = "1.3.0"

class Logger:
    """日志管理器"""
    
    _logger = None
    
    @classmethod
    def setup_logger(cls) -> logging.Logger:
        """设置日志记录器"""
        if cls._logger is None:
            cls._logger = logging.getLogger('bws_cli')
            cls._logger.setLevel(logging.INFO)
            
            # 避免重复添加handler
            if not cls._logger.handlers:
                # 创建文件handler
                file_handler = logging.FileHandler('bws_reservation.log', encoding='utf-8')
                file_handler.setLevel(logging.INFO)
                
                # 创建控制台handler
                console_handler = logging.StreamHandler()
                console_handler.setLevel(logging.INFO)
                
                # 创建格式器（精确到毫秒）
                file_formatter = logging.Formatter('%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
                console_formatter = logging.Formatter('%(asctime)s.%(msecs)03d - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
                
                file_handler.setFormatter(file_formatter)
                console_handler.setFormatter(console_formatter)
                
                # 添加handler到logger
                cls._logger.addHandler(file_handler)
                cls._logger.addHandler(console_handler)
        
        return cls._logger
    
    @classmethod
    def info(cls, message: str) -> None:
        """输出信息级别日志"""
        if cls._logger is None:
            cls.setup_logger()
        cls._logger.info(message)
    
    @classmethod
    def error(cls, message: str) -> None:
        """输出错误级别日志"""
        if cls._logger is None:
            cls.setup_logger()
        cls._logger.error(message)
    
    @classmethod
    def warning(cls, message: str) -> None:
        """输出警告级别日志"""
        if cls._logger is None:
            cls.setup_logger()
        cls._logger.warning(message)
    
    @classmethod
    def log_to_file_only(cls, message: str, level: str = 'INFO') -> None:
        """仅写入文件的日志，不在控制台显示"""
        if cls._logger is None:
            cls.setup_logger()
        
        # 创建一个临时的只有文件handler的logger
        file_only_logger = logging.getLogger('bws_cli_file_only')
        file_only_logger.setLevel(logging.INFO)
        
        # 避免重复添加handler
        if not file_only_logger.handlers:
            file_handler = logging.FileHandler('bws_reservation.log', encoding='utf-8')
            file_handler.setLevel(logging.INFO)
            file_formatter = logging.Formatter('%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
            file_handler.setFormatter(file_formatter)
            file_only_logger.addHandler(file_handler)
        
        if level.upper() == 'ERROR':
            file_only_logger.error(message)
        else:
            file_only_logger.info(message)


class TimeUtils:
    """时间工具类"""
    _use_ntp = False
    _ntp_offset = 0
    
    @staticmethod
    def set_ntp_mode(use_ntp: bool = True):
        """设置是否使用 NTP 时间"""
        TimeUtils._use_ntp = use_ntp
        if use_ntp:
            TimeUtils._sync_ntp_time()
    
    @staticmethod
    def _sync_ntp_time():
        """同步 NTP 时间，计算时间偏移"""
        try:
            # 使用阿里云 NTP 服务器
            ntp_client = ntplib.NTPClient()
            response = ntp_client.request('ntp.aliyun.com', version=3)
            ntp_time = response.tx_time
            local_time = time.time()
            TimeUtils._ntp_offset = ntp_time - local_time
            Logger.info(f"NTP 校时成功，时间偏移: {TimeUtils._ntp_offset:.3f}秒")
        except Exception as e:
            Logger.error(f"NTP 校时失败: {e}，将使用本地时间")
            TimeUtils._use_ntp = False
            TimeUtils._ntp_offset = 0
    
    @staticmethod
    def get_current_time() -> float:
        """获取当前时间（支持NTP校时）"""
        if TimeUtils._use_ntp:
            return time.time() + TimeUtils._ntp_offset
        return time.time()
    
    @staticmethod
    def timestamp_to_datetime(timestamp: int) -> str:
        """将时间戳转换为可读的日期时间格式"""
        return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


class CookieParser:
    """Cookie解析器"""
    @staticmethod
    def parse_cookie_string(cookie_string: str) -> Dict[str, str]:
        """解析Cookie字符串为字典"""
        cookies = {}
        for cookie_item in cookie_string.split(';'):
            if '=' in cookie_item:
                key, value = cookie_item.split('=', 1)
                cookies[key.strip()] = value.strip()
        return cookies


class CookieCache:
    """Cookie缓存管理器"""
    
    CACHE_FILE = "cookie_cache.json"
    
    @classmethod
    def save_cookie(cls, cookie_string: str) -> None:
        """保存Cookie到缓存文件"""
        try:
            cache_data = {
                "cookie": cookie_string,
                "timestamp": int(time.time())
            }
            with open(cls.CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            Logger.error(f"保存Cookie缓存失败: {e}")
    
    @classmethod
    def load_cookie(cls) -> Optional[str]:
        """从缓存文件加载Cookie"""
        try:
            if not os.path.exists(cls.CACHE_FILE):
                return None
            
            with open(cls.CACHE_FILE, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 检查缓存是否过期（7天）
            cache_age = int(time.time()) - cache_data.get('timestamp', 0)
            if cache_age > 7 * 24 * 3600:  # 7天过期
                Logger.warning("Cookie缓存已过期，需要重新输入")
                return None
            
            return cache_data.get('cookie')
        except Exception as e:
            Logger.error(f"读取Cookie缓存失败: {e}")
            return None
    
    @classmethod
    def clear_cache(cls) -> None:
        """清除缓存文件"""
        try:
            if os.path.exists(cls.CACHE_FILE):
                os.remove(cls.CACHE_FILE)
        except Exception as e:
            Logger.error(f"清除Cookie缓存失败: {e}")


class QRCodeLogin:
    """二维码登录功能类"""
    
    @staticmethod
    def tvsign(params, appkey='4409e2ce8ffd12b8', appsec='59b43e04ad6965f34319062b478f83dd'):
        """为请求参数进行 api 签名"""
        params.update({'appkey': appkey})
        params = dict(sorted(params.items()))  # 重排序参数 key
        query = urllib.parse.urlencode(params)  # 序列化参数
        sign = hashlib.md5((query+appsec).encode()).hexdigest()  # 计算 api 签名
        params.update({'sign': sign})
        return params
    
    @staticmethod
    def show_qr_popup(qr_url):
        """直接打开二维码图片"""
        def show_image():
            try:
                # 生成二维码图片
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=4,
                )
                qr.add_data(qr_url)
                qr.make(fit=True)
                
                # 创建二维码图片
                qr_img = qr.make_image(fill_color="black", back_color="white")
                
                # 直接显示图片（会使用系统默认图片查看器打开）
                qr_img.show()
                    
            except Exception as e:
                Logger.error(f"显示二维码图片失败: {e}")
        
        # 在新线程中显示图片，避免阻塞主程序
        show_thread = threading.Thread(target=show_image, daemon=True)
        show_thread.start()
        return show_thread
    
    @staticmethod
    def login_with_qrcode():
        """通过二维码登录获取Cookie"""
        try:
            Logger.info("正在获取二维码...")
            
            # 获取二维码
            loginInfo = requests.post(
                'https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code',
                params=QRCodeLogin.tvsign({
                    'local_id': '0',
                    'ts': int(time.time())
                }),
                headers={
                    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                },
                timeout=10
            ).json()
            
            if loginInfo.get('code') != 0:
                Logger.error(f"获取二维码失败: {loginInfo.get('message', '未知错误')}")
                return None
            
            # 生成二维码
            print("\n请使用哔哩哔哩手机客户端扫描以下二维码登录：")
            print("="*60)
            qrcode_terminal.draw(loginInfo['data']['url'])
            print("="*60)
            
            # 同时打开二维码图片
            Logger.info("正在打开二维码图片...")
            QRCodeLogin.show_qr_popup(loginInfo['data']['url'])
            
            Logger.info("等待扫码登录...")
            
            # 轮询登录状态
            auth_code = loginInfo['data']['auth_code']
            while True:
                try:
                    pollInfo = requests.post(
                        'https://passport.bilibili.com/x/passport-tv-login/qrcode/poll',
                        params=QRCodeLogin.tvsign({
                            'auth_code': auth_code,
                            'local_id': '0',
                            'ts': int(time.time())
                        }),
                        headers={
                            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                        },
                        timeout=10
                    ).json()
                    
                    if pollInfo['code'] == 0:
                        # 登录成功
                        loginData = pollInfo['data']
                        Logger.info(f"登录成功！有效期至 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() + int(loginData['expires_in'])))}")
                        
                        # 提取Cookie信息
                        cookie_info = loginData.get('cookie_info', {})
                        cookies = cookie_info.get('cookies', [])
                        
                        # 构建Cookie字符串
                        cookie_parts = []
                        for cookie in cookies:
                            cookie_parts.append(f"{cookie['name']}={cookie['value']}")
                        
                        cookie_string = '; '.join(cookie_parts)
                        
                        if not cookie_string:
                            Logger.error("获取Cookie失败：登录响应中没有Cookie信息")
                            return None
                        
                        # 验证Cookie有效性
                        api_client = BilibiliAPI(cookie_string)
                        if api_client.validate_cookie():
                            Logger.info("Cookie验证成功，正在保存到缓存...")
                            CookieCache.save_cookie(cookie_string)
                            return cookie_string
                        else:
                            Logger.error("获取的Cookie无效")
                            return None
                        
                    elif pollInfo['code'] == -3:
                        Logger.error('API校验密匙错误')
                        return None
                    elif pollInfo['code'] == -400:
                        Logger.error('请求错误')
                        return None
                    elif pollInfo['code'] == 86038:
                        Logger.error('二维码已失效，请重新获取')
                        return None
                    elif pollInfo['code'] == 86039:
                        # 二维码未确认，继续等待
                        time.sleep(2)
                        continue
                    else:
                        Logger.error(f'未知错误: {pollInfo.get("message", "未知错误")}')
                        return None
                        
                except requests.RequestException as e:
                    Logger.error(f"网络请求失败: {e}")
                    time.sleep(2)
                    continue
                except KeyboardInterrupt:
                    Logger.info("\n用户取消扫码登录")
                    return None
                    
        except Exception as e:
            Logger.error(f"扫码登录过程中发生错误: {e}")
            return None


class BilibiliAPI:
    """哔哩哔哩API客户端"""
    
    BASE_URL = "https://api.bilibili.com/x/activity/bws/online/park/reserve"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/540.36 (KHTML, like Gecko)"
    
    def __init__(self, cookie_string: str):
        self.cookies = CookieParser.parse_cookie_string(cookie_string)
        self._validate_cookies()
        self.csrf_token = self.cookies['bili_jct']
        self.session = self._create_session()
    
    def _validate_cookies(self) -> None:
        """验证必要的Cookie是否存在"""
        if 'bili_jct' not in self.cookies:
            raise ValueError("Cookie中缺少必要的bili_jct字段")
    
    def _create_session(self) -> requests.Session:
        """创建HTTP会话"""
        session = requests.Session()
        session.headers.update({"User-Agent": self.USER_AGENT})
        return session
    
    def get_reservation_info(self, reserve_dates: str = "20250711,20250712,20240713") -> Optional[Dict]:
        """获取预约信息"""
        url = f"{self.BASE_URL}/info"
        params = {
            "csrf": self.csrf_token,
            "reserve_date": reserve_dates
        }
        
        try:
            response = self.session.get(url, params=params, cookies=self.cookies)
            response.raise_for_status()
            result = response.json()
            
            if result['code'] != 0:
                Logger.error(f"API错误: {result['code']} 消息: {result['message']}")
                return None
            return result['data']
        except requests.RequestException as e:
            Logger.error(f"网络请求失败: {e}")
            return None
    
    def make_reservation(self, ticket_number: str, reservation_id: int) -> Dict:
        """进行预约"""
        url = f"{self.BASE_URL}/do"
        data = {
            "ticket_no": ticket_number,
            "csrf": self.csrf_token,
            "inter_reserve_id": reservation_id
        }
        
        # 记录请求发起时间（仅写入文件）
        request_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        Logger.log_to_file_only(f"请求发起时间: {request_time} | 请求URL: {url} | 请求数据: {data}")
        
        try:
            response = self.session.post(url, data=data, cookies=self.cookies)
            response.raise_for_status()
            result = response.json()
            
            # 记录响应正文内容（仅写入文件）
            Logger.log_to_file_only(f"响应正文内容: {json.dumps(result, ensure_ascii=False)}")
            
            return result
        except requests.RequestException as e:
            error_result = {"code": -1, "message": f"网络请求失败: {e}"}
            Logger.log_to_file_only(f"网络请求失败: {e}", 'ERROR')
            return error_result
    
    def validate_cookie(self) -> bool:
        """验证Cookie是否有效"""
        try:
            # 尝试获取预约信息来验证Cookie有效性
            result = self.get_reservation_info()
            return result is not None
        except Exception:
            return False


class ReservationData:
    """预约数据管理类"""
    
    def __init__(self, reservation_info: Dict):
        self.raw_data = reservation_info
        self.ticket_days = list(reservation_info['user_reserve_info'].keys())
        self.ticket_mapping = self._build_ticket_mapping()
        self.activity_mapping = self._build_activity_mapping()
    
    def _build_ticket_mapping(self) -> Dict[str, str]:
        """构建日期到票号的映射"""
        return {day: self.raw_data['user_ticket_info'][day]['ticket'] 
                for day in self.ticket_days}
    
    def _build_activity_mapping(self) -> Dict[int, Tuple[str, int, int]]:
        """构建活动ID到活动信息的映射"""
        activity_map = {}
        for day in self.ticket_days:
            for activity in self.raw_data['reserve_list'][day]:
                activity_id = activity['reserve_id']
                title = activity['act_title'].replace('\n', '')
                start_time = activity['act_begin_time']
                reserve_time = activity['reserve_begin_time']
                activity_map[activity_id] = (title, start_time, reserve_time)
        return activity_map
    
    def display_ticket_info(self) -> None:
        """显示购票信息"""
        Logger.info("当前账号 BW 购票信息：")
        for day in self.ticket_days:
            ticket_info = self.raw_data['user_ticket_info'][day]
            Logger.info(f"{ticket_info['screen_name']} | 票种：{ticket_info['sku_name']} | 电子票号：{ticket_info['ticket']}")
    
    def display_activities(self) -> None:
        """显示活动信息"""
        Logger.info('')
        for day in self.ticket_days:
            for activity in self.raw_data['reserve_list'][day]:
                activity_id = activity['reserve_id']
                title = activity['act_title'].replace('\n', '')
                reserve_time_str = TimeUtils.timestamp_to_datetime(activity['reserve_begin_time'])
                start_time_str = TimeUtils.timestamp_to_datetime(activity['act_begin_time'])
                Logger.info(f"活动代码：{activity_id}  {title} 预约：{reserve_time_str} 开始：{start_time_str}")
            Logger.info('')
    
    def display_activities_for_date(self, selected_date: str) -> None:
        """显示指定日期的活动信息"""
        if selected_date not in self.raw_data['reserve_list']:
            Logger.error(f"未找到日期 {selected_date} 的活动信息")
            return
        
        ticket_info = self.raw_data['user_ticket_info'][selected_date]
        Logger.info(f"\n{ticket_info['screen_name']} - {ticket_info['sku_name']}")
        Logger.info(f"电子票号：{ticket_info['ticket']}\n")
        
        activities = self.raw_data['reserve_list'][selected_date]
        for activity in activities:
            activity_id = activity['reserve_id']
            title = activity['act_title'].replace('\n', '')
            reserve_time_str = TimeUtils.timestamp_to_datetime(activity['reserve_begin_time'])
            start_time_str = TimeUtils.timestamp_to_datetime(activity['act_begin_time'])
            Logger.info(f"活动代码：{activity_id}")
            Logger.info(f"活动名称：{title}")
            Logger.info(f"预约时间：{reserve_time_str}")
            Logger.info(f"开始时间：{start_time_str}")
            if '预约只是签售资格，现场签售需购买up主周边。' in activity['describe_info']:
                warning = "\033[31m【BWS Ticket 提示】注意，该项目可能包含需要付费的内容，请在预约前留意！\033[0m"
            else:
                warning = activity['describe_info'].replace('\n', ' ')
            Logger.info(f"活动提示：{warning}")
            Logger.info("-" * 50)
    
    def get_ticket_for_activity(self, activity_id: int) -> Optional[str]:
        """根据活动ID获取对应的票号"""
        if activity_id not in self.activity_mapping:
            return None
        
        activity_start_time = self.activity_mapping[activity_id][1]
        activity_date = datetime.datetime.fromtimestamp(activity_start_time).strftime("%Y%m%d")
        return self.ticket_mapping.get(activity_date)


class ReservationBot:
    """预约机器人"""
    
    def __init__(self, api_client: BilibiliAPI, reservation_data: ReservationData):
        self.api_client = api_client
        self.reservation_data = reservation_data
    
    def wait_and_reserve(self, activity_id: int, mode: str = "scheduled") -> None:
        """等待并进行预约
        
        Args:
            activity_id: 活动ID
            mode: 预约模式 ('scheduled' 准时开抢, 'immediate' 直接开抢)
        """
        activity_info = self.reservation_data.activity_mapping[activity_id]
        activity_title, start_time, reserve_time = activity_info
        
        ticket_number = self.reservation_data.get_ticket_for_activity(activity_id)
        if not ticket_number:
            Logger.error(f"无法找到活动 {activity_id} 对应的票号")
            return
        
        Logger.info(f'一次抢一个，如果觉得抢的少请自行多开。')
        Logger.info(f'开始预约：{activity_title}')
        
        if mode == "immediate":
            Logger.info("直接开抢模式：立即开始抢票")
            self._start_reservation_loop(ticket_number, activity_id, activity_title)
        else:
            Logger.info("准时开抢模式：等待预约时间")
            self._wait_for_reservation_time(ticket_number, activity_id, activity_title, reserve_time)
    
    def _wait_for_reservation_time(self, ticket_number: str, activity_id: int, activity_title: str, reserve_time: int) -> None:
        """等待预约时间到达"""
        last_status_time = 0
        auto_sync_done = False
        
        while True:
            current_time = int(TimeUtils.get_current_time())
            
            # 开抢前5分钟自动校时
            if not auto_sync_done and current_time >= reserve_time - 300:  # 5分钟 = 300秒
                auto_sync_done = True
                Logger.info("开抢前 5 分钟，正在进行自动 NTP 校时...")
                
                # 记录校时前的时间（如果已启用 NTP 则使用当前 NTP 时间，否则使用本机时间）
                time_before = TimeUtils.get_current_time()
                local_time_before = time.time()  # 始终记录本机时间用于显示真实的本机与NTP差异
                
                # 执行NTP校时
                try:
                    ntp_client = ntplib.NTPClient()
                    response = ntp_client.request('ntp.aliyun.com', version=3)
                    ntp_time = response.tx_time
                    
                    # 计算本机时间与NTP服务器的真实时间差（用于显示）
                    real_time_diff = ntp_time - local_time_before
                    
                    # 计算新的NTP偏移（基于本机时间）
                    new_ntp_offset = ntp_time - local_time_before
                    
                    # 显示本机时间与NTP服务器的真实时间差
                    if abs(real_time_diff) < 1:
                        Logger.info(f"NTP 校时完成，本机时间与NTP服务器时间差：{real_time_diff:.3f}秒 (时间同步良好)")
                    else:
                        Logger.info(f"NTP 校时完成，本机时间与NTP服务器时间差：{real_time_diff:.3f}秒 (建议检查系统时间)")
                    
                    # 如果用户未开启NTP模式，根据时间差决定是否临时应用校时
                    if not TimeUtils._use_ntp:
                        if abs(real_time_diff) > 0.7:
                            TimeUtils._ntp_offset = new_ntp_offset
                            TimeUtils._use_ntp = True
                            Logger.info(f"本机时间偏差较大({real_time_diff:.3f}秒)，已临时启用 NTP 校时模式以确保抢票时间准确")
                        else:
                            Logger.info(f"本机时间偏差较小({real_time_diff:.3f}秒)，继续使用本机时间")
                    else:
                        # 更新现有的NTP偏移
                        old_offset = TimeUtils._ntp_offset
                        TimeUtils._ntp_offset = new_ntp_offset
                        offset_change = new_ntp_offset - old_offset
                        Logger.info(f"已更新 NTP 时间偏移 (偏移变化: {offset_change:.3f}秒)")
                        
                except Exception as e:
                    Logger.warning(f"自动 NTP 校时失败: {e}，将使用当前时间模式")
            
            # 等待预约开始时间
            if current_time <= reserve_time:
                if (current_time > last_status_time + 3 and 
                    last_status_time + 30 < reserve_time):
                    last_status_time = current_time
                    remaining_seconds = reserve_time - current_time
                    reserve_time_str = TimeUtils.timestamp_to_datetime(reserve_time)
                    time_source = "NTP 时间" if TimeUtils._use_ntp else "本地时间"
                    Logger.info(f'等待开票，当前预约活动：{activity_title} | 开票时间：{reserve_time_str} | 剩余：{remaining_seconds}秒 ({time_source})')
                time.sleep(0.1)
                continue
            
            # 时间到达，开始抢票
            self._start_reservation_loop(ticket_number, activity_id, activity_title)
            break
    
    def _simulate_wait_for_reservation_time(self, ticket_number: str, activity_id: int, activity_title: str, reserve_time: int) -> None:
        """模拟等待预约时间到达（测试用）"""
        last_status_time = 0
        auto_sync_done = False
        
        Logger.info("=== 模拟测试模式 ===")
        Logger.info("此模式仅用于测试 NTP 校时功能，不会执行真正的抢票操作")
        
        while True:
            current_time = int(TimeUtils.get_current_time())
            
            # 开抢前5分钟自动校时（模拟）
            if not auto_sync_done and current_time >= reserve_time - 300:  # 5分钟 = 300秒
                auto_sync_done = True
                Logger.info("[模拟] 开抢前5分钟，正在进行自动NTP校时...")
                
                # 记录校时前的时间（如果已启用NTP则使用当前NTP时间，否则使用本机时间）
                time_before = TimeUtils.get_current_time()
                local_time_before = time.time()  # 始终记录本机时间用于显示真实的本机与NTP差异
                
                # 执行NTP校时
                try:
                    ntp_client = ntplib.NTPClient()
                    response = ntp_client.request('ntp.aliyun.com', version=3)
                    ntp_time = response.tx_time
                    
                    # 计算本机时间与NTP服务器的真实时间差（用于显示）
                    real_time_diff = ntp_time - local_time_before
                    
                    # 计算新的NTP偏移（基于本机时间）
                    new_ntp_offset = ntp_time - local_time_before
                    
                    # 显示详细的时间信息（测试模式）
                    current_program_time = TimeUtils.get_current_time()
                    current_local_time = time.time()
                    
                    Logger.info(f"[模拟] === 时间信息详情 ===")
                    Logger.info(f"[模拟] 程序时间: {TimeUtils.timestamp_to_datetime(current_program_time)}")
                    Logger.info(f"[模拟] NTP 时间:  {TimeUtils.timestamp_to_datetime(ntp_time)}")
                    Logger.info(f"[模拟] 本机时间: {TimeUtils.timestamp_to_datetime(current_local_time)}")
                    Logger.info(f"[模拟] 本机与 NTP 时间差: {real_time_diff:.3f}秒")
                    
                    if abs(real_time_diff) < 1:
                        Logger.info(f"[模拟] NTP 校时完成 (时间同步良好)")
                    else:
                        Logger.info(f"[模拟] NTP 校时完成 (建议检查系统时间)")
                    
                    # 如果用户未开启NTP模式，根据时间差决定是否临时应用校时
                    if not TimeUtils._use_ntp:
                        if abs(real_time_diff) > 0.7:
                            TimeUtils._ntp_offset = new_ntp_offset
                            TimeUtils._use_ntp = True
                            Logger.info(f"[模拟] 本机时间偏差较大({real_time_diff:.3f}秒)，已临时启用 NTP 校时模式")
                        else:
                            Logger.info(f"[模拟] 本机时间偏差较小({real_time_diff:.3f}秒)，继续使用本机时间")
                    else:
                        # 更新现有的NTP偏移
                        old_offset = TimeUtils._ntp_offset
                        TimeUtils._ntp_offset = new_ntp_offset
                        offset_change = new_ntp_offset - old_offset
                        Logger.info(f"[模拟] 已更新 NTP 时间偏移 (偏移变化: {offset_change:.3f}秒)")
                        
                except Exception as e:
                    Logger.warning(f"[模拟] 自动 NTP 校时失败: {e}，将使用当前时间模式")
            
            # 等待预约开始时间
            if current_time <= reserve_time:
                if (current_time > last_status_time + 3 and 
                    last_status_time + 30 < reserve_time):
                    last_status_time = current_time
                    remaining_seconds = reserve_time - current_time
                    reserve_time_str = TimeUtils.timestamp_to_datetime(reserve_time)
                    time_source = "NTP时间" if TimeUtils._use_ntp else "本地时间"
                    Logger.info(f'[模拟] 等待开票，当前预约活动：{activity_title} | 开票时间：{reserve_time_str} | 剩余：{remaining_seconds}秒 ({time_source})')
                time.sleep(0.1)
                continue
            
            # 时间到达，模拟开始抢票（不执行真正的抢票）
            Logger.info(f"[模拟] 时间到达！模拟开始抢票：{activity_title}")
            Logger.info("[模拟] 这是测试模式，未执行真正的抢票操作")
            Logger.info("[模拟] 测试完成！NTP 校时功能工作正常")
            break
    
    def _start_reservation_loop(self, ticket_number: str, activity_id: int, activity_title: str) -> None:
        """开始预约循环"""
        Logger.info(f"开始抢票：{activity_title}\n")
        
        while True:
            try:
                result = self.api_client.make_reservation(ticket_number, activity_id)
                
                code = result.get("code")
                if code == 0:
                    Logger.info("预约成功！")
                    break
                elif code == 75637:
                    Logger.info("[75637] 尚未开放，请等待预约开始")
                elif code == -702:
                    Logger.warning("[702] 请求频率太快")
                elif code == -1:
                    Logger.error("[-1] 网络错误，继续重试")
                else:
                    Logger.warning(f"出金了，是新的未知状态，请自行判断：{result}")
                
                time.sleep(0.25)
            except KeyboardInterrupt:
                Logger.info("用户中断抢票")
                break
            except Exception as e:
                Logger.error(f"预约过程中发生错误：{e}")
                time.sleep(1)


class InteractiveMenu:
    """交互式菜单类"""
    
    @staticmethod
    def clear_screen():
        """清屏"""
        os.system('cls' if os.name == 'nt' else 'clear')
    
    @staticmethod
    def show_menu(title: str, options: list, selected_index: int = 0) -> int:
        """显示菜单并返回选择的索引"""
        try:
            questions = [
                inquirer.List('choice',
                            message=title,
                            choices=options,
                            default=options[selected_index] if 0 <= selected_index < len(options) else options[0])
            ]
            answers = inquirer.prompt(questions)
            
            if answers is None:  # 用户按了 Ctrl+C
                return -1
            
            # 返回选择的索引
            return options.index(answers['choice'])
        except (KeyboardInterrupt, EOFError):
            return -1
    
    @staticmethod
    def show_date_menu(reservation_data) -> str:
        """显示日期选择菜单"""
        options = []
        date_mapping = {}
        
        for i, day in enumerate(reservation_data.ticket_days):
            ticket_info = reservation_data.raw_data['user_ticket_info'][day]
            display_text = f"{ticket_info['screen_name']} - {ticket_info['sku_name']}"
            options.append(display_text)
            date_mapping[i] = day
        
        if not options:
            print("\n没有可用的活动日期")
            input("按回车键返回主菜单...")
            return None
        
        selected_index = InteractiveMenu.show_menu("选择查看日期", options)
        if selected_index == -1:
            return None
        
        return date_mapping[selected_index]
    
    @staticmethod
    def show_activity_menu(reservation_data, selected_date: str) -> int:
        """显示活动选择菜单"""
        options = []
        activity_mapping = {}
        
        activities = reservation_data.raw_data['reserve_list'][selected_date]
        for i, activity in enumerate(activities):
            activity_id = activity['reserve_id']
            title = activity['act_title'].replace('\n', '')
            reserve_time_str = TimeUtils.timestamp_to_datetime(activity['reserve_begin_time'])
            start_time_str = TimeUtils.timestamp_to_datetime(activity['act_begin_time'])
            if '预约只是签售资格，现场签售需购买up主周边。' in activity['describe_info']:
                warning = "[包含付费内容] "
            else:
                warning = ""
            display_text = f"\033[31m{warning}\033[0m{title} | 预约开始 {reserve_time_str} | 活动时间 {start_time_str}"
            options.append(display_text)
            activity_mapping[i] = activity_id
        
        if not options:
            print(f"\n{selected_date} 没有可用的活动")
            input("按回车键返回主菜单...")
            return None
        
        selected_index = InteractiveMenu.show_menu(f"{selected_date} 活动列表", options)
        if selected_index == -1:
            return None
        
        return activity_mapping[selected_index]
    
    @staticmethod
    def show_reservation_mode_menu() -> str:
        """显示预约模式选择菜单"""
        options = [
            "准时开抢 - 等待预约时间到达后开始抢票",
            "直接开抢 - 立即开始抢票（忽略预约时间）"
        ]
        
        selected_index = InteractiveMenu.show_menu("选择预约模式", options)
        if selected_index == -1:
            return None
        
        return "scheduled" if selected_index == 0 else "immediate"


class UserInterface:
    """用户界面类"""
    
    @staticmethod
    def show_welcome_message() -> None:
        """显示欢迎信息"""
        Logger.info("""
██████╗ ██╗    ██╗███████╗    ████████╗██╗ ██████╗██╗  ██╗███████╗████████╗
██╔══██╗██║    ██║██╔════╝    ╚══██╔══╝██║██╔════╝██║ ██╔╝██╔════╝╚══██╔══╝
██████╔╝██║ █╗ ██║███████╗       ██║   ██║██║     █████╔╝ █████╗     ██║   
██╔══██╗██║███╗██║╚════██║       ██║   ██║██║     ██╔═██╗ ██╔══╝     ██║   
██████╔╝╚███╔███╔╝███████║       ██║   ██║╚██████╗██║  ██╗███████╗   ██║   
╚═════╝  ╚══╝╚══╝ ╚══════╝       ╚═╝   ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝   ╚═╝   
        """)
        Logger.info(f'当前程序版本：{VERSION} | 本工具在 Starsbon/bws_ticket 开源，欢迎 Star！')
        Logger.info('')
    

    
    @staticmethod
    def get_valid_cookie() -> str:
        """获取有效的 Cookie（优先使用缓存）"""
        # 尝试从缓存加载Cookie
        cached_cookie = CookieCache.load_cookie()
        
        if cached_cookie:
            Logger.info("发现 Cookie 缓存，正在验证有效性...")
            try:
                # 验证缓存的Cookie是否有效
                api_client = BilibiliAPI(cached_cookie)
                if api_client.validate_cookie():
                    Logger.info("Cookie 缓存有效，直接使用缓存登录\n")
                    return cached_cookie
                else:
                    Logger.warning("Cookie 缓存已失效，需要重新登录\n")
                    CookieCache.clear_cache()
            except Exception as e:
                Logger.error(f"验证 Cookie 缓存时出错: {e}")
                CookieCache.clear_cache()
        
        # 如果没有缓存或缓存失效，提供登录选项
        while True:
            try:
                login_options = [
                    "扫码登录（推荐）",
                    "手动输入Cookie"
                ]
                
                selected_index = InteractiveMenu.show_menu("请选择登录方式", login_options)
                
                if selected_index == -1:  # ESC退出
                    Logger.info("用户取消登录")
                    exit(0)
                elif selected_index == 0:  # 扫码登录
                    Logger.info("选择扫码登录方式")
                    cookie_string = QRCodeLogin.login_with_qrcode()
                    if cookie_string:
                        return cookie_string
                    else:
                        Logger.warning("扫码登录失败，请重试或选择其他登录方式")
                        continue
                elif selected_index == 1:  # 手动输入Cookie
                    Logger.info("选择手动输入Cookie方式")
                    Logger.info("获取方法：登录bilibili.com后，按F12打开开发者工具，在Network标签页找到任意请求，复制Cookie值")
                    
                    cookie_string = input('请输入Cookie: ').strip()
                    if not cookie_string:
                        Logger.warning("Cookie不能为空，请重新选择登录方式")
                        continue
                    
                    # 验证Cookie
                    api_client = BilibiliAPI(cookie_string)
                    if api_client.validate_cookie():
                        Logger.info("Cookie 验证成功，正在保存到缓存...\n")
                        CookieCache.save_cookie(cookie_string)
                        return cookie_string
                    else:
                        Logger.warning("Cookie 无效，请重新选择登录方式")
                        continue
                        
            except KeyboardInterrupt:
                Logger.info("\n用户取消登录")
                exit(0)
            except Exception as e:
                Logger.error(f"登录过程中发生错误: {e}，请重试")
                continue
    
    # @staticmethod
    # def get_activity_selection(activity_mapping: Dict[int, Tuple[str, int, int]]) -> int:
    #     """获取活动选择"""
    #     while True:
    #         try:
    #             activity_input = input('输入活动代码：')
    #             activity_id = int(activity_input)
                
    #             if activity_id not in activity_mapping:
    #                 Logger.warning('选择不正确！请重新输入。')
    #                 continue
                
    #             activity_title = activity_mapping[activity_id][0]
    #             Logger.info(f'{activity_id}：{activity_title} 已选中')
    #             return activity_id
                
    #         except ValueError:
    #             Logger.warning('请输入有效的数字！')


def main():
    """主函数"""
    try:
        # 初始化日志系统
        logger = Logger.setup_logger()

        # 显示欢迎信息
        UserInterface.show_welcome_message()
        
        # 获取有效的Cookie（优先使用缓存）
        cookie_string = UserInterface.get_valid_cookie()
        api_client = BilibiliAPI(cookie_string)
        
        # 获取预约信息
        reservation_info = api_client.get_reservation_info()
        if not reservation_info:
            Logger.error('账号信息错误或异常，请检查 网络/账号/Cookies 再试，详细报错见上方。')
            return
        
        # 初始化数据管理器
        reservation_data = ReservationData(reservation_info)
        
        # 主菜单循环
        while True:
            time_status = "NTP时间" if TimeUtils._use_ntp else "本地时间"
            main_options = [
                "查看所有活动信息",
                "按日期查看活动",
                "开始预约抢票",
                f"时间校时设置 (当前: {time_status})",
                "模拟测试 (6分钟倒计时测试)",
                "退出程序"
            ]
            
            selected_index = InteractiveMenu.show_menu("BWS Ticket - 主菜单", main_options)
            
            if selected_index == -1 or selected_index == 5:  # ESC或退出
                Logger.info("程序退出")
                break
            elif selected_index == 0:  # 查看所有活动信息
                print("\n" + "="*60)
                print("查看所有活动信息")
                print("="*60)
                reservation_data.display_ticket_info()
                reservation_data.display_activities()
                input("\n按回车键返回主菜单...")
            elif selected_index == 4:  # 模拟测试
                Logger.info("开始模拟测试 - 6分钟倒计时（包含5分钟自动校时）")
                Logger.info("注意：这是测试模式，不会执行真正的抢票操作")
                
                # 使用 inquirer 进行确认
                try:
                    confirm_question = [
                        inquirer.Confirm('confirm',
                                       message="确认开始模拟测试？",
                                       default=False)
                    ]
                    confirm_answer = inquirer.prompt(confirm_question)
                    
                    if confirm_answer and confirm_answer['confirm']:
                        print("\n" + "="*60)
                        Logger.info("开始模拟测试")
                        Logger.info("模拟活动：测试活动")
                        Logger.info("模拟模式：准时开抢（6分钟倒计时）")
                        Logger.info("按 Ctrl+C 可以中断测试\n")
                        
                        # 创建模拟的预约时间（当前时间 + 6分钟）
                        current_time = int(TimeUtils.get_current_time())
                        mock_reserve_time = current_time + 360  # 6分钟 = 360秒
                         
                        # 创建临时的ReservationBot实例用于测试
                        test_bot = ReservationBot(api_client, reservation_data)
                         
                        # 执行模拟测试
                        test_bot._simulate_wait_for_reservation_time("TEST123", 99999, "测试活动", mock_reserve_time)
                        
                except (KeyboardInterrupt, EOFError):
                    pass
                
                input("\n测试结束，按回车键返回主菜单...")
            elif selected_index == 1:  # 按日期查看活动
                selected_date = InteractiveMenu.show_date_menu(reservation_data)
                if selected_date:
                    print("\n" + "="*60)
                    print(f"查看 {selected_date} 活动信息")
                    print("="*60)
                    reservation_data.display_activities_for_date(selected_date)
                    input("\n按回车键返回主菜单...")
            elif selected_index == 2:  # 开始预约抢票
                # 选择日期
                selected_date = InteractiveMenu.show_date_menu(reservation_data)
                if not selected_date:
                    continue
                
                # 选择活动
                selected_activity_id = InteractiveMenu.show_activity_menu(reservation_data, selected_date)
                if not selected_activity_id:
                    continue
                
                # 选择预约模式
                reservation_mode = InteractiveMenu.show_reservation_mode_menu()
                if not reservation_mode:
                    continue
                
                # 显示预约确认信息
                activity_title = reservation_data.activity_mapping[selected_activity_id][0]
                mode_text = "准时开抢" if reservation_mode == "scheduled" else "直接开抢"
                
                # print("\n" + "="*60)
                # print("预约确认")
                # print("="*60)
                # print(f"活动名称：{activity_title}")
                # print(f"预约模式：{mode_text}")
                # print("按 Ctrl+C 可以中断抢票")
                
                # 使用 inquirer 进行确认
                try:
                    confirm_question = [
                        inquirer.Confirm('confirm',
                                       message="确认开始预约？",
                                       default=False)
                    ]
                    confirm_answer = inquirer.prompt(confirm_question)
                    
                    if not confirm_answer or not confirm_answer['confirm']:
                        continue
                except (KeyboardInterrupt, EOFError):
                    continue
                
                # 开始预约
                print("\n" + "="*60)
                Logger.info(f"开始预约：{activity_title}")
                Logger.info(f"预约模式：{mode_text}")
                Logger.info("按 Ctrl+C 可以中断抢票\n")
                
                bot = ReservationBot(api_client, reservation_data)
                bot.wait_and_reserve(selected_activity_id, reservation_mode)
                
                input("\n预约结束，按回车键返回主菜单...")
            elif selected_index == 3:  # 时间校时设置
                time_options = [
                    "使用本地时间",
                    "使用 Aliyun NTP 时间"
                ]
                
                current_mode = 1 if TimeUtils._use_ntp else 0
                time_selected = InteractiveMenu.show_menu("选择时间模式", time_options, current_mode)
                
                if time_selected == -1:
                    continue
                elif time_selected == 0:
                    TimeUtils.set_ntp_mode(False)
                    Logger.info("已切换到本地时间模式")
                elif time_selected == 1:
                    Logger.info("正在进行NTP校时...")
                    TimeUtils.set_ntp_mode(True)
                
                input("\n按回车键返回主菜单...")
        
    except ValueError as e:
        Logger.error(f"配置错误: {e}")
    except KeyboardInterrupt:
        Logger.info("\n用户取消操作")
    except Exception as e:
        Logger.error(f"程序运行出错: {e}")
        input("按回车键退出...")


if __name__ == '__main__':
    main()
