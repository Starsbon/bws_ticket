import datetime
import time
import requests
import json
import os
import logging
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bws_reservation.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)


class TimeUtils:
    """时间工具类"""
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


class ConfigManager:
    """配置管理器"""
    
    CONFIG_FILE = "bws_config.json"
    DEFAULT_CONFIG = {
        "retry_intervals": {
            "normal": 0.25,
            "rate_limit": 0.5,
            "not_open": 1.0
        },
        "max_retries": 1000,
        "request_timeout": 10,
        "ui_settings": {
            "window_size": "900x700",
            "auto_save_pool": True
        }
    }
    
    @classmethod
    def load_config(cls) -> Dict:
        """加载配置"""
        try:
            if os.path.exists(cls.CONFIG_FILE):
                with open(cls.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                # 合并默认配置
                return {**cls.DEFAULT_CONFIG, **config}
            return cls.DEFAULT_CONFIG.copy()
        except Exception as e:
            logging.warning(f"加载配置失败，使用默认配置: {e}")
            return cls.DEFAULT_CONFIG.copy()
    
    @classmethod
    def save_config(cls, config: Dict) -> None:
        """保存配置"""
        try:
            with open(cls.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"保存配置失败: {e}")


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
            logging.info("Cookie缓存保存成功")
        except Exception as e:
            logging.error(f"保存Cookie缓存失败: {e}")
    
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
                return None
            
            return cache_data.get('cookie')
        except Exception:
            return None
    
    @classmethod
    def clear_cache(cls) -> None:
        """清除缓存文件"""
        try:
            if os.path.exists(cls.CACHE_FILE):
                os.remove(cls.CACHE_FILE)
        except Exception:
            pass


class BilibiliAPI:
    """哔哩哔哩API客户端"""
    
    BASE_URL = "https://api.bilibili.com/x/activity/bws/online/park/reserve"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/540.36 (KHTML, like Gecko)"
    
    def __init__(self, cookie_string: str, config: Dict = None):
        self.config = config or ConfigManager.load_config()
        self.cookies = CookieParser.parse_cookie_string(cookie_string)
        self._validate_cookies()
        self.csrf_token = self.cookies['bili_jct']
        self.session = self._create_session()
        self.retry_count = 0
    
    def _validate_cookies(self) -> None:
        """验证必要的Cookie是否存在"""
        if 'bili_jct' not in self.cookies:
            raise ValueError("Cookie中缺少必要的bili_jct字段")
    
    def _create_session(self) -> requests.Session:
        """创建HTTP会话"""
        session = requests.Session()
        session.headers.update({"User-Agent": self.USER_AGENT})
        # 设置超时
        session.timeout = self.config.get('request_timeout', 10)
        return session
    
    def get_reservation_info(self, reserve_dates: str = "20250711,20250712,20240713") -> Optional[Dict]:
        """获取预约信息"""
        url = f"{self.BASE_URL}/info"
        params = {
            "csrf": self.csrf_token,
            "reserve_date": reserve_dates
        }
        
        try:
            response = self.session.get(url, params=params, cookies=self.cookies, 
                                      timeout=self.config.get('request_timeout', 10))
            response.raise_for_status()
            result = response.json()
            
            if result['code'] != 0:
                logging.warning(f"获取预约信息失败: {result.get('message', '未知错误')}")
                return None
            logging.info("成功获取预约信息")
            return result['data']
        except requests.RequestException as e:
            logging.error(f"获取预约信息网络请求失败: {e}")
            return None
    
    def make_reservation(self, ticket_number: str, reservation_id: int) -> Dict:
        """进行预约"""
        url = f"{self.BASE_URL}/do"
        data = {
            "ticket_no": ticket_number,
            "csrf": self.csrf_token,
            "inter_reserve_id": reservation_id
        }
        
        try:
            response = self.session.post(url, data=data, cookies=self.cookies,
                                       timeout=self.config.get('request_timeout', 10))
            response.raise_for_status()
            result = response.json()
            
            # 处理特定错误码
            if result.get('code') == 75637:
                result['message'] = "尚未开放预约，请等待预约时间开始"
                logging.info(f"活动 {reservation_id} 尚未开放预约")
            elif result.get('code') == 702:
                result['message'] = "请求速度过快，正在重试..."
                logging.warning(f"活动 {reservation_id} 请求速度过快")
            elif result.get('code') == 0:
                logging.info(f"活动 {reservation_id} 预约成功！")
            else:
                logging.warning(f"活动 {reservation_id} 预约失败: {result.get('message', '未知错误')}")
            
            return result
        except requests.RequestException as e:
            logging.error(f"预约请求网络失败: {e}")
            return {"code": -1, "message": f"网络请求失败: {e}"}
    
    def validate_cookie(self) -> bool:
        """验证Cookie是否有效"""
        try:
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
    
    def get_ticket_for_activity(self, activity_id: int) -> Optional[str]:
        """根据活动ID获取对应的票号"""
        if activity_id not in self.activity_mapping:
            return None
        
        activity_start_time = self.activity_mapping[activity_id][1]
        activity_date = datetime.datetime.fromtimestamp(activity_start_time).strftime("%Y%m%d")
        return self.ticket_mapping.get(activity_date)


class ReservationPool:
    """预约池管理器"""    
    POOL_CACHE_FILE = "reservation_pool.json"
    
    def __init__(self, api_client: BilibiliAPI, reservation_data: ReservationData, config: Dict = None):
        self.api_client = api_client
        self.reservation_data = reservation_data
        self.config = config or ConfigManager.load_config()
        self.selected_activities = set()
        self.is_running = False
        self.executor = None
        self.retry_counts = {}  # 记录每个活动的重试次数
    
    def add_activity(self, activity_id: int) -> bool:
        """添加活动到预约池"""
        if activity_id in self.reservation_data.activity_mapping:
            self.selected_activities.add(activity_id)
            self.retry_counts[activity_id] = 0
            self._save_pool_state()
            logging.info(f"活动 {activity_id} 已添加到预约池")
            return True
        return False
    
    def remove_activity(self, activity_id: int) -> None:
        """从预约池移除活动"""
        self.selected_activities.discard(activity_id)
        self.retry_counts.pop(activity_id, None)
        self._save_pool_state()
        logging.info(f"活动 {activity_id} 已从预约池移除")
    
    def clear_pool(self) -> None:
        """清空预约池"""
        self.selected_activities.clear()
        self.retry_counts.clear()
        self._save_pool_state()
        logging.info("预约池已清空")
    
    def get_selected_activities(self) -> List[int]:
        """获取已选择的活动列表"""
        return list(self.selected_activities)
    
    def start_concurrent_reservation(self, status_callback=None, wait_for_time=True) -> None:
        """开始并发预约"""
        if self.is_running or not self.selected_activities:
            return
        
        self.is_running = True
        self.executor = ThreadPoolExecutor(max_workers=len(self.selected_activities))
        
        # 为每个活动创建预约任务
        futures = []
        for activity_id in self.selected_activities:
            future = self.executor.submit(self._reserve_activity, activity_id, status_callback, wait_for_time)
            futures.append((activity_id, future))
        
        # 监控任务完成情况
        def monitor_tasks():
            for activity_id, future in futures:
                try:
                    result = future.result()
                    if status_callback:
                        status_callback(activity_id, result)
                except Exception as e:
                    if status_callback:
                        status_callback(activity_id, {"code": -1, "message": str(e)})
            
            self.is_running = False
            if self.executor:
                self.executor.shutdown(wait=False)
        
        threading.Thread(target=monitor_tasks, daemon=True).start()
    
    def _reserve_activity(self, activity_id: int, status_callback=None, wait_for_time=True) -> Dict:
        """预约单个活动"""
        activity_info = self.reservation_data.activity_mapping[activity_id]
        activity_title, start_time, reserve_time = activity_info
        
        ticket_number = self.reservation_data.get_ticket_for_activity(activity_id)
        if not ticket_number:
            return {"code": -1, "message": "无法找到对应的票号"}
        
        # 根据参数决定是否等待预约开始时间
        if wait_for_time:
            while int(time.time()) <= reserve_time:
                if not self.is_running:  # 检查是否被停止
                    return {"code": -1, "message": "预约被取消"}
                time.sleep(0.1)
        
        # 开始预约
        while self.is_running:
            result = self.api_client.make_reservation(ticket_number, activity_id)
            
            # 预约成功
            if result.get("code") == 0:
                return result
            
            # 处理特定错误码
            error_code = result.get("code")
            self.retry_counts[activity_id] = self.retry_counts.get(activity_id, 0) + 1
            
            # 检查最大重试次数
            max_retries = self.config.get('max_retries', 1000)
            if self.retry_counts[activity_id] >= max_retries:
                logging.warning(f"活动 {activity_id} 达到最大重试次数 {max_retries}，停止重试")
                return {"code": -1, "message": f"达到最大重试次数 {max_retries}"}
            
            if error_code == 75637:  # 尚未开放预约
                if status_callback:
                    status_callback(activity_id, {"code": -2, "message": "等待预约开放中..."})
                time.sleep(self.config['retry_intervals']['not_open'])
            elif error_code == 702:  # 请求速度太快
                time.sleep(self.config['retry_intervals']['rate_limit'])
            else:
                time.sleep(self.config['retry_intervals']['normal'])
        
        return {"code": -1, "message": "预约被停止"}
    
    def stop_reservation(self) -> None:
        """停止预约"""
        self.is_running = False
        if self.executor:
            self.executor.shutdown(wait=False)
        logging.info("预约已停止")
    
    def _save_pool_state(self) -> None:
        """保存预约池状态"""
        if not self.config.get('ui_settings', {}).get('auto_save_pool', True):
            return
        
        try:
            pool_data = {
                "selected_activities": list(self.selected_activities),
                "timestamp": int(time.time())
            }
            with open(self.POOL_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(pool_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"保存预约池状态失败: {e}")
    
    def load_pool_state(self) -> List[int]:
        """加载预约池状态"""
        try:
            if not os.path.exists(self.POOL_CACHE_FILE):
                return []
            
            with open(self.POOL_CACHE_FILE, 'r', encoding='utf-8') as f:
                pool_data = json.load(f)
            
            # 检查缓存是否过期（1天）
            cache_age = int(time.time()) - pool_data.get('timestamp', 0)
            if cache_age > 24 * 3600:  # 1天过期
                return []
            
            return pool_data.get('selected_activities', [])
        except Exception as e:
            logging.error(f"加载预约池状态失败: {e}")
            return []


class BWSReservationGUI:
    """B站预约GUI主界面"""
    
    def __init__(self):
        self.config = ConfigManager.load_config()
        
        self.root = tk.Tk()
        self.root.title("B站活动预约工具 - GUI版 v2.0")
        window_size = self.config.get('ui_settings', {}).get('window_size', '900x700')
        self.root.geometry(window_size)
        
        self.api_client = None
        self.reservation_data = None
        self.reservation_pool = None
        
        self.setup_ui()
        self.load_cached_cookie()
        
        # 绑定窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def setup_ui(self):
        """设置用户界面"""
        # 创建主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Cookie输入区域
        cookie_frame = ttk.LabelFrame(main_frame, text="Cookie设置", padding="10")
        cookie_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        ttk.Label(cookie_frame, text="B站Cookie:").grid(row=0, column=0, sticky=tk.W)
        self.cookie_entry = tk.Text(cookie_frame, height=3, width=80)
        self.cookie_entry.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(5, 0))
        
        button_frame = ttk.Frame(cookie_frame)
        button_frame.grid(row=2, column=0, columnspan=2, pady=(10, 0))
        
        self.validate_btn = ttk.Button(button_frame, text="验证Cookie", command=self.validate_cookie)
        self.validate_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.clear_cache_btn = ttk.Button(button_frame, text="清除缓存", command=self.clear_cookie_cache)
        self.clear_cache_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.config_btn = ttk.Button(button_frame, text="设置", command=self.open_config_window)
        self.config_btn.pack(side=tk.LEFT)
        
        # 创建标签页
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        
        # 第一个标签页：活动选择
        self.selection_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.selection_frame, text="活动选择")
        
        # 活动列表区域
        activity_frame = ttk.LabelFrame(self.selection_frame, text="活动列表", padding="10")
        activity_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=10)
        
        # 活动列表
        self.activity_tree = ttk.Treeview(activity_frame, columns=('title', 'reserve_time', 'start_time'), show='tree headings', height=20)
        self.activity_tree.heading('#0', text='活动ID')
        self.activity_tree.heading('title', text='活动标题')
        self.activity_tree.heading('reserve_time', text='预约时间')
        self.activity_tree.heading('start_time', text='开始时间')
        
        self.activity_tree.column('#0', width=80)
        self.activity_tree.column('title', width=400)
        self.activity_tree.column('reserve_time', width=150)
        self.activity_tree.column('start_time', width=150)
        
        scrollbar_activity = ttk.Scrollbar(activity_frame, orient=tk.VERTICAL, command=self.activity_tree.yview)
        self.activity_tree.configure(yscrollcommand=scrollbar_activity.set)
        
        self.activity_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar_activity.grid(row=0, column=1, sticky=(tk.N, tk.S))
        
        # 活动操作按钮
        activity_btn_frame = ttk.Frame(activity_frame)
        activity_btn_frame.grid(row=1, column=0, columnspan=2, pady=(10, 0))
        
        self.add_btn = ttk.Button(activity_btn_frame, text="添加到预约池", command=self.add_to_pool, state=tk.DISABLED)
        self.add_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # 第二个标签页：预约池和日志
        self.reservation_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.reservation_frame, text="预约管理")
        
        # 预约池区域（左侧，50%宽度）
        pool_frame = ttk.LabelFrame(self.reservation_frame, text="预约池", padding="10")
        pool_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(10, 5), pady=10)
        
        # 预约池列表
        self.pool_tree = ttk.Treeview(pool_frame, columns=('title',), show='tree headings', height=15)
        self.pool_tree.heading('#0', text='活动ID')
        self.pool_tree.heading('title', text='活动标题')
        
        self.pool_tree.column('#0', width=80)
        self.pool_tree.column('title', width=300)
        
        scrollbar_pool = ttk.Scrollbar(pool_frame, orient=tk.VERTICAL, command=self.pool_tree.yview)
        self.pool_tree.configure(yscrollcommand=scrollbar_pool.set)
        
        self.pool_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar_pool.grid(row=0, column=1, sticky=(tk.N, tk.S))
        
        # 预约池操作按钮
        pool_btn_frame = ttk.Frame(pool_frame)
        pool_btn_frame.grid(row=1, column=0, columnspan=2, pady=(10, 0))
        
        self.remove_btn = ttk.Button(pool_btn_frame, text="从预约池移除", command=self.remove_from_pool, state=tk.DISABLED)
        self.remove_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.clear_pool_btn = ttk.Button(pool_btn_frame, text="清空预约池", command=self.clear_pool, state=tk.DISABLED)
        self.clear_pool_btn.pack(side=tk.LEFT)
        
        # 预约控制区域
        control_frame = ttk.LabelFrame(pool_frame, text="预约控制", padding="10")
        control_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 0))
        
        self.scheduled_btn = ttk.Button(control_frame, text="定时预约", command=self.start_scheduled_reservation, state=tk.DISABLED)
        self.scheduled_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.immediate_btn = ttk.Button(control_frame, text="立即开始预约", command=self.start_immediate_reservation, state=tk.DISABLED)
        self.immediate_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.stop_btn = ttk.Button(control_frame, text="停止预约", command=self.stop_reservation, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)
        
        # 日志区域（右侧，50%宽度）
        log_frame = ttk.LabelFrame(self.reservation_frame, text="预约日志", padding="10")
        log_frame.grid(row=0, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(5, 10), pady=10)
        
        self.status_text = scrolledtext.ScrolledText(log_frame, height=25, width=50)
        self.status_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # 日志操作按钮
        log_btn_frame = ttk.Frame(log_frame)
        log_btn_frame.grid(row=1, column=0, pady=(10, 0))
        
        self.clear_log_btn = ttk.Button(log_btn_frame, text="清空日志", command=self.clear_log)
        self.clear_log_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.save_log_btn = ttk.Button(log_btn_frame, text="保存日志", command=self.save_log)
        self.save_log_btn.pack(side=tk.LEFT)
        
        # 配置网格权重
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)
        
        # 活动选择页面权重
        self.selection_frame.columnconfigure(0, weight=1)
        self.selection_frame.rowconfigure(0, weight=1)
        activity_frame.columnconfigure(0, weight=1)
        activity_frame.rowconfigure(0, weight=1)
        
        # 预约管理页面权重（5:5比例）
        self.reservation_frame.columnconfigure(0, weight=1)
        self.reservation_frame.columnconfigure(1, weight=1)
        self.reservation_frame.rowconfigure(0, weight=1)
        
        pool_frame.columnconfigure(0, weight=1)
        pool_frame.rowconfigure(0, weight=1)
        
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        
        cookie_frame.columnconfigure(0, weight=1)
    
    def load_cached_cookie(self):
        """加载缓存的Cookie"""
        cached_cookie = CookieCache.load_cookie()
        if cached_cookie:
            self.cookie_entry.delete(1.0, tk.END)
            self.cookie_entry.insert(1.0, cached_cookie)
            self.log_status("发现Cookie缓存，请点击验证Cookie按钮")
    
    def validate_cookie(self):
        """验证Cookie"""
        cookie_string = self.cookie_entry.get(1.0, tk.END).strip()
        if not cookie_string:
            messagebox.showerror("错误", "请输入Cookie")
            return
        
        try:
            self.api_client = BilibiliAPI(cookie_string)
            if self.api_client.validate_cookie():
                self.log_status("Cookie验证成功，正在获取活动信息...")
                CookieCache.save_cookie(cookie_string)
                self.load_activities()
            else:
                messagebox.showerror("错误", "Cookie无效，请检查后重新输入")
                self.log_status("Cookie验证失败")
        except Exception as e:
            messagebox.showerror("错误", f"Cookie验证失败: {e}")
            self.log_status(f"Cookie验证出错: {e}")
    
    def clear_cookie_cache(self):
        """清除Cookie缓存"""
        CookieCache.clear_cache()
        self.log_status("Cookie缓存已清除")
        messagebox.showinfo("提示", "Cookie缓存已清除")
    
    def load_activities(self):
        """加载活动信息"""
        if not self.api_client:
            return
        
        reservation_info = self.api_client.get_reservation_info()
        if not reservation_info:
            messagebox.showerror("错误", "获取活动信息失败")
            return
        
        self.reservation_data = ReservationData(reservation_info)
        self.reservation_pool = ReservationPool(self.api_client, self.reservation_data, self.config)
        
        # 尝试恢复预约池状态
        self.restore_pool_state()
        
        # 清空活动列表
        for item in self.activity_tree.get_children():
            self.activity_tree.delete(item)
        
        # 添加活动到列表
        for activity_id, (title, start_time, reserve_time) in self.reservation_data.activity_mapping.items():
            reserve_time_str = TimeUtils.timestamp_to_datetime(reserve_time)
            start_time_str = TimeUtils.timestamp_to_datetime(start_time)
            
            self.activity_tree.insert('', 'end', text=str(activity_id), 
                                    values=(title, reserve_time_str, start_time_str))
        
        # 启用相关按钮
        self.add_btn.config(state=tk.NORMAL)
        self.clear_pool_btn.config(state=tk.NORMAL)
        
        self.log_status(f"成功加载 {len(self.reservation_data.activity_mapping)} 个活动")
    
    def restore_pool_state(self):
        """恢复预约池状态"""
        if not self.reservation_pool:
            return
        
        saved_activities = self.reservation_pool.load_pool_state()
        if saved_activities:
            restored_count = 0
            for activity_id in saved_activities:
                if activity_id in self.reservation_data.activity_mapping:
                    if self.reservation_pool.add_activity(activity_id):
                        title = self.reservation_data.activity_mapping[activity_id][0]
                        self.pool_tree.insert('', 'end', text=str(activity_id), values=(title,))
                        restored_count += 1
            
            if restored_count > 0:
                self.update_pool_buttons()
                self.log_status(f"已恢复 {restored_count} 个活动到预约池")
    
    def open_config_window(self):
        """打开配置窗口"""
        config_window = tk.Toplevel(self.root)
        config_window.title("设置")
        config_window.geometry("400x500")
        config_window.transient(self.root)
        config_window.grab_set()
        
        # 重试间隔设置
        retry_frame = ttk.LabelFrame(config_window, text="重试间隔设置 (秒)", padding="10")
        retry_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(retry_frame, text="正常重试间隔:").grid(row=0, column=0, sticky=tk.W, pady=2)
        normal_var = tk.StringVar(value=str(self.config['retry_intervals']['normal']))
        ttk.Entry(retry_frame, textvariable=normal_var, width=10).grid(row=0, column=1, padx=(10, 0))
        
        ttk.Label(retry_frame, text="限速重试间隔:").grid(row=1, column=0, sticky=tk.W, pady=2)
        rate_limit_var = tk.StringVar(value=str(self.config['retry_intervals']['rate_limit']))
        ttk.Entry(retry_frame, textvariable=rate_limit_var, width=10).grid(row=1, column=1, padx=(10, 0))
        
        ttk.Label(retry_frame, text="未开放重试间隔:").grid(row=2, column=0, sticky=tk.W, pady=2)
        not_open_var = tk.StringVar(value=str(self.config['retry_intervals']['not_open']))
        ttk.Entry(retry_frame, textvariable=not_open_var, width=10).grid(row=2, column=1, padx=(10, 0))
        
        # 其他设置
        other_frame = ttk.LabelFrame(config_window, text="其他设置", padding="10")
        other_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(other_frame, text="最大重试次数:").grid(row=0, column=0, sticky=tk.W, pady=2)
        max_retries_var = tk.StringVar(value=str(self.config['max_retries']))
        ttk.Entry(other_frame, textvariable=max_retries_var, width=10).grid(row=0, column=1, padx=(10, 0))
        
        ttk.Label(other_frame, text="请求超时 (秒):").grid(row=1, column=0, sticky=tk.W, pady=2)
        timeout_var = tk.StringVar(value=str(self.config['request_timeout']))
        ttk.Entry(other_frame, textvariable=timeout_var, width=10).grid(row=1, column=1, padx=(10, 0))
        
        # UI设置
        ui_frame = ttk.LabelFrame(config_window, text="界面设置", padding="10")
        ui_frame.pack(fill=tk.X, padx=10, pady=5)
        
        auto_save_var = tk.BooleanVar(value=self.config['ui_settings']['auto_save_pool'])
        ttk.Checkbutton(ui_frame, text="自动保存预约池状态", variable=auto_save_var).pack(anchor=tk.W)
        
        # 按钮
        btn_frame = ttk.Frame(config_window)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        def save_config():
            try:
                self.config['retry_intervals']['normal'] = float(normal_var.get())
                self.config['retry_intervals']['rate_limit'] = float(rate_limit_var.get())
                self.config['retry_intervals']['not_open'] = float(not_open_var.get())
                self.config['max_retries'] = int(max_retries_var.get())
                self.config['request_timeout'] = int(timeout_var.get())
                self.config['ui_settings']['auto_save_pool'] = auto_save_var.get()
                
                ConfigManager.save_config(self.config)
                messagebox.showinfo("提示", "配置保存成功")
                config_window.destroy()
                self.log_status("配置已更新")
            except ValueError as e:
                messagebox.showerror("错误", "请输入有效的数值")
        
        def reset_config():
            if messagebox.askyesno("确认", "确定要重置为默认配置吗？"):
                self.config = ConfigManager.DEFAULT_CONFIG.copy()
                ConfigManager.save_config(self.config)
                config_window.destroy()
                self.log_status("配置已重置为默认值")
        
        ttk.Button(btn_frame, text="保存", command=save_config).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(btn_frame, text="重置默认", command=reset_config).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(btn_frame, text="取消", command=config_window.destroy).pack(side=tk.LEFT)
    
    def add_to_pool(self):
        """添加活动到预约池"""
        selection = self.activity_tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要添加的活动")
            return
        
        for item in selection:
            activity_id = int(self.activity_tree.item(item, 'text'))
            if self.reservation_pool.add_activity(activity_id):
                title = self.reservation_data.activity_mapping[activity_id][0]
                self.pool_tree.insert('', 'end', text=str(activity_id), values=(title,))
        
        self.update_pool_buttons()
        self.log_status(f"已添加 {len(selection)} 个活动到预约池")
    
    def remove_from_pool(self):
        """从预约池移除活动"""
        selection = self.pool_tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要移除的活动")
            return
        
        for item in selection:
            activity_id = int(self.pool_tree.item(item, 'text'))
            self.reservation_pool.remove_activity(activity_id)
            self.pool_tree.delete(item)
        
        self.update_pool_buttons()
        self.log_status(f"已从预约池移除 {len(selection)} 个活动")
    
    def clear_pool(self):
        """清空预约池"""
        if self.reservation_pool:
            self.reservation_pool.clear_pool()
        
        for item in self.pool_tree.get_children():
            self.pool_tree.delete(item)
        
        self.update_pool_buttons()
        self.log_status("预约池已清空")
    
    def update_pool_buttons(self):
        """更新预约池相关按钮状态"""
        has_items = len(self.pool_tree.get_children()) > 0
        self.remove_btn.config(state=tk.NORMAL if has_items else tk.DISABLED)
        self.scheduled_btn.config(state=tk.NORMAL if has_items else tk.DISABLED)
        self.immediate_btn.config(state=tk.NORMAL if has_items else tk.DISABLED)
    
    def start_scheduled_reservation(self):
        """开始定时预约（等待预约开始时间）"""
        if not self.reservation_pool or not self.reservation_pool.selected_activities:
            messagebox.showwarning("提示", "预约池为空，请先添加活动")
            return
        
        self.scheduled_btn.config(state=tk.DISABLED)
        self.immediate_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        
        selected_count = len(self.reservation_pool.selected_activities)
        self.log_status(f"开始定时预约 {selected_count} 个活动，将在预约时间开始后自动预约...")
        
        self.reservation_pool.start_concurrent_reservation(self.on_reservation_status, wait_for_time=True)
    
    def start_immediate_reservation(self):
        """开始立即预约（忽略预约开始时间）"""
        if not self.reservation_pool or not self.reservation_pool.selected_activities:
            messagebox.showwarning("提示", "预约池为空，请先添加活动")
            return
        
        self.scheduled_btn.config(state=tk.DISABLED)
        self.immediate_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        
        selected_count = len(self.reservation_pool.selected_activities)
        self.log_status(f"开始立即预约 {selected_count} 个活动...")
        
        self.reservation_pool.start_concurrent_reservation(self.on_reservation_status, wait_for_time=False)
    
    def stop_reservation(self):
        """停止预约"""
        if self.reservation_pool:
            self.reservation_pool.stop_reservation()
        
        self.scheduled_btn.config(state=tk.NORMAL)
        self.immediate_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        
        self.log_status("预约已停止")
    
    def on_reservation_status(self, activity_id: int, result: Dict):
        """预约状态回调"""
        activity_title = self.reservation_data.activity_mapping[activity_id][0]
        
        if result.get("code") == 0:
            status_msg = f"✅ 活动 {activity_id} ({activity_title}) 预约成功！"
        else:
            error_msg = result.get("message", "未知错误")
            status_msg = f"❌ 活动 {activity_id} ({activity_title}) 预约失败: {error_msg}"
        
        # 在主线程中更新UI
        self.root.after(0, lambda: self.log_status(status_msg))
    
    def clear_log(self):
        """清空日志"""
        self.status_text.delete(1.0, tk.END)
        self.log_status("日志已清空")
    
    def save_log(self):
        """保存日志到文件"""
        try:
            from tkinter import filedialog
            import datetime
            
            # 默认文件名包含时间戳
            default_filename = f"bws_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            
            filename = filedialog.asksaveasfilename(
                title="保存日志",
                defaultextension=".txt",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
                initialvalue=default_filename
            )
            
            if filename:
                log_content = self.status_text.get(1.0, tk.END)
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(log_content)
                self.log_status(f"日志已保存到: {filename}")
                messagebox.showinfo("成功", f"日志已保存到:\n{filename}")
        except Exception as e:
            error_msg = f"保存日志失败: {str(e)}"
            self.log_status(error_msg)
            messagebox.showerror("错误", error_msg)
    
    def log_status(self, message: str):
        """记录状态信息"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}\n"
        
        self.status_text.insert(tk.END, log_message)
        self.status_text.see(tk.END)
    
    def on_closing(self):
        """窗口关闭事件处理"""
        if self.reservation_pool and self.reservation_pool.is_running:
            if messagebox.askyesno("确认退出", "预约正在进行中，确定要退出吗？"):
                self.reservation_pool.stop_reservation()
                self.root.destroy()
        else:
            self.root.destroy()
    
    def run(self):
        """运行GUI"""
        try:
            self.root.mainloop()
        except Exception as e:
            logging.error(f"GUI运行出错: {e}")
            messagebox.showerror("错误", f"程序运行出错: {e}")


def main():
    """主函数"""
    app = BWSReservationGUI()
    app.run()


if __name__ == '__main__':
    main()