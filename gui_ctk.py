import os
import json
import threading
import tkinter
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
import subprocess
import sys
import csv
import time
import psutil
from typing import Dict

from register_kling_bitbrowser import run_batch, read_rows
from ip_manager import IPManager
from email_manager import EmailManager

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('KL-账号批量注册工具')
        self.geometry('1100x800')
        ctk.set_appearance_mode('dark')
        ctk.set_default_color_theme('blue')
        self.attributes('-topmost', False)
        if getattr(sys, 'frozen', False):
            self.base_dir = os.path.dirname(__file__)
            self.exe_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.dirname(__file__)
            self.exe_dir = self.base_dir
        
        # Initialize Managers
        self.ip_manager = IPManager(os.path.join(self.exe_dir, 'ip_pool.json'))
        self.email_manager = EmailManager(os.path.join(self.exe_dir, 'email_pool.json'))
        
        # Performance Monitoring & Debounce
        self.last_refresh_time = 0
        self.debounce_timer = None
        self.manual_refresh_mode = False
        self.resource_check_interval = 5000 # Check resources every 5s
        self.auto_refresh_interval = 30000 # Default 30s light poll
        self.refresh_stats_perf = [] # Store last 10 refresh durations

        # Use exe_dir for config/user files, base_dir for bundled resources
        self.csv_var = tkinter.StringVar(value=os.path.join(self.exe_dir, 'kl-mail.csv'))
        if not os.path.exists(self.csv_var.get()):
             self.csv_var.set(os.path.join(self.base_dir, 'kl-mail.csv'))


        self.xpath_var = tkinter.StringVar(value=os.path.join(self.exe_dir, 'kling_xpaths.json'))
        if not os.path.exists(self.xpath_var.get()):
             self.xpath_var.set(os.path.join(self.base_dir, 'kling_xpaths.json'))

        self.bit_url_var = tkinter.StringVar(value='http://127.0.0.1:54345')
        self.bit_secret_var = tkinter.StringVar(value=os.environ.get('BITBROWSER_SECRET') or '')
        self.platform_url_var = tkinter.StringVar(value='https://klingai.com/global')
        self.concurrent_var = tkinter.IntVar(value=1)
        self.timeout_ms_var = tkinter.IntVar(value=100000)
        self.poll_ms_var = tkinter.IntVar(value=500)
        self.max_rounds_var = tkinter.IntVar(value=5)
        self.build_output_var = tkinter.StringVar(value=os.path.join(self.exe_dir, 'dist'))
        self.enable_xpath_var = tkinter.BooleanVar(value=True)
        self.cnt_total_var = tkinter.StringVar(value='0')
        self.cnt_success_var = tkinter.StringVar(value='0')
        self.cnt_fail_var = tkinter.StringVar(value='0')
        self.stop_event = threading.Event()
        self.worker = None
        
        # Variables for IP Manager GUI
        self.ip_config_path_var = tkinter.StringVar(value=self.ip_manager.config_path)
        self.ip_max_usage_var = tkinter.IntVar(value=self.ip_manager.get_max_usage())
        
        # Default IP Settings
        self.ip_def_port_var = tkinter.StringVar()
        self.ip_def_user_var = tkinter.StringVar()
        self.ip_def_pass_var = tkinter.StringVar()
        self.ip_def_protocol_var = tkinter.StringVar(value='socks5')
        
        # Registration Count Control
        self.target_reg_count_var = tkinter.StringVar(value='10')
        self.max_reg_count_var = tkinter.StringVar(value='0')
        self.realtime_count_var = tkinter.StringVar(value='0 / 0 / 0') # Success / Target / Max
        self.config_path_var = tkinter.StringVar(value=os.path.join(self.exe_dir, 'gui_config.json'))
        
        # Auto-save traces
        for var in [self.xpath_var, self.bit_url_var, self.bit_secret_var, self.platform_url_var,
                   self.concurrent_var, self.timeout_ms_var, self.poll_ms_var, self.max_rounds_var,
                   self.build_output_var, self.ip_def_port_var, self.ip_def_user_var, self.ip_def_pass_var, 
                   self.ip_def_protocol_var, self.ip_config_path_var, self.config_path_var]:
            var.trace_add('write', lambda *args: self.save_config())

        self._build_ui()
        self.append_log(f"程序运行目录 (exe_dir): {self.exe_dir}")
        self.append_log(f"基础资源目录 (base_dir): {self.base_dir}")
        self._load_config()
        self.refresh_ip_stats()

    def _build_ui(self):
        # Configure Treeview Style for Dark Mode
        style = ttk.Style()
        style.theme_use("clam")
        
        # Colors (Dark Theme)
        bg_color = "#2b2b2b"
        fg_color = "#ffffff"
        field_bg = "#2b2b2b"
        header_bg = "#1f1f1f"
        select_bg = "#1f6aa5"
        
        style.configure("Treeview", 
                        background=bg_color,
                        foreground=fg_color,
                        fieldbackground=field_bg,
                        borderwidth=0,
                        font=("Arial", 11)) # Reduced from 12
        
        style.configure("Treeview.Heading",
                        background=header_bg,
                        foreground=fg_color,
                        relief="flat",
                        font=("Arial", 12, "bold")) # Reduced from 13
                        
        style.map("Treeview",
                  background=[('selected', select_bg)])
                  
        style.map("Treeview.Heading",
                  background=[('active', "#333333")])

        # Create Tabview
        self.tabview = ctk.CTkTabview(self, width=1080, height=780)
        self.tabview.pack(padx=10, pady=10, fill='both', expand=True)
        
        self.tab_reg = self.tabview.add("注册任务")
        self.tab_ip = self.tabview.add("IP池管理")
        self.tab_email = self.tabview.add("邮箱池管理")
        
        self._build_registration_tab()
        self._build_ip_pool_tab(self.tab_ip)
        self._build_email_pool_tab()

    def _build_registration_tab(self):
        parent = self.tab_reg
        
        # --- Top Section: Stats & Controls ---
        top_frame = ctk.CTkFrame(parent, fg_color="transparent")
        top_frame.pack(fill='x', padx=10, pady=(10, 5))
        
        # Left: Statistics Panel (Resource & Task)
        stats_panel = ctk.CTkFrame(top_frame)
        stats_panel.pack(side='left', fill='both', expand=True, padx=(0, 10))
        
        # Resource Stats Section
        res_frame = ctk.CTkFrame(stats_panel, fg_color="transparent")
        res_frame.pack(fill='x', padx=10, pady=5)
        
        ctk.CTkLabel(res_frame, text="资源统计", font=("Arial", 20, "bold")).pack(anchor='w')
        
        res_grid = ctk.CTkFrame(res_frame, fg_color="transparent")
        res_grid.pack(fill='x', pady=5)
        
        # Grid for Resources: Available IP | Available Email | Max Register
        # Labels
        ctk.CTkLabel(res_grid, text="可用IP", font=("Arial", 14), text_color="gray").grid(row=0, column=0, padx=10, sticky="ew")
        ctk.CTkLabel(res_grid, text="可用邮箱", font=("Arial", 14), text_color="gray").grid(row=0, column=1, padx=10, sticky="ew")
        ctk.CTkLabel(res_grid, text="最大可注册", font=("Arial", 14), text_color="gray").grid(row=0, column=2, padx=10, sticky="ew")
            
        # Values
        self.home_ip_stat_label = ctk.CTkLabel(res_grid, text="0", font=("Arial", 22, "bold"), text_color="#ff4d4d")
        self.home_ip_stat_label.grid(row=1, column=0, padx=10, sticky="ew")
        
        self.home_email_stat_label = ctk.CTkLabel(res_grid, text="0", font=("Arial", 22, "bold"), text_color="#1E90FF")
        self.home_email_stat_label.grid(row=1, column=1, padx=10, sticky="ew")
        
        self.lbl_max_reg = ctk.CTkLabel(res_grid, textvariable=self.max_reg_count_var, font=("Arial", 22, "bold"), text_color="#FFA500")
        self.lbl_max_reg.grid(row=1, column=2, padx=10, sticky="ew")
        
        # Manual Refresh Button (Hidden by default)
        self.btn_manual_refresh = ctk.CTkButton(res_frame, text="刷新", width=60, height=24, 
                                                command=lambda: self.trigger_refresh(force=True))
        # Place it relative to res_frame
        self.btn_manual_refresh.place(relx=0.95, rely=0.05, anchor='ne')
        self.btn_manual_refresh.pack_forget() # Hide initially
        
        res_grid.grid_columnconfigure(0, weight=1)
        res_grid.grid_columnconfigure(1, weight=1)
        res_grid.grid_columnconfigure(2, weight=1)

        ctk.CTkFrame(stats_panel, height=2, fg_color="gray").pack(fill='x', padx=10, pady=5) # Separator

        # Task Status Section
        task_frame = ctk.CTkFrame(stats_panel, fg_color="transparent")
        task_frame.pack(fill='x', padx=10, pady=5)
        
        ctk.CTkLabel(task_frame, text="任务状态", font=("Arial", 20, "bold")).pack(anchor='w')
        
        task_grid = ctk.CTkFrame(task_frame, fg_color="transparent")
        task_grid.pack(fill='x', pady=5)
        
        # Target Input Row
        tgt_box = ctk.CTkFrame(task_grid, fg_color="transparent")
        tgt_box.grid(row=0, column=0, columnspan=3, pady=(0, 10), sticky="w")
        ctk.CTkLabel(tgt_box, text="本次目标数量:", font=("Arial", 16)).pack(side='left', padx=(10, 5))
        ctk.CTkEntry(tgt_box, textvariable=self.target_reg_count_var, width=100, font=("Arial", 16)).pack(side='left')

        # Progress Grid: Success | Target | Max
        # Labels
        ctk.CTkLabel(task_grid, text="成功", font=("Arial", 14), text_color="gray").grid(row=1, column=0, padx=10, sticky="ew")
        ctk.CTkLabel(task_grid, text="目标", font=("Arial", 14), text_color="gray").grid(row=1, column=1, padx=10, sticky="ew")
        ctk.CTkLabel(task_grid, text="最大", font=("Arial", 14), text_color="gray").grid(row=1, column=2, padx=10, sticky="ew")
        
        # Values (Need to parse realtime_count_var or use separate vars. 
        # Current var is "S / T / M". Let's use separate labels updated by refresh)
        self.lbl_prog_success = ctk.CTkLabel(task_grid, text="0", font=("Arial", 22, "bold"), text_color="#2cc985")
        self.lbl_prog_success.grid(row=2, column=0, padx=10, sticky="ew")
        
        self.lbl_prog_target = ctk.CTkLabel(task_grid, textvariable=self.target_reg_count_var, font=("Arial", 22, "bold"))
        self.lbl_prog_target.grid(row=2, column=1, padx=10, sticky="ew")
        
        self.lbl_prog_max = ctk.CTkLabel(task_grid, textvariable=self.max_reg_count_var, font=("Arial", 22, "bold"))
        self.lbl_prog_max.grid(row=2, column=2, padx=10, sticky="ew")

        task_grid.grid_columnconfigure(0, weight=1)
        task_grid.grid_columnconfigure(1, weight=1)
        task_grid.grid_columnconfigure(2, weight=1)

        # Right: Action Panel (Buttons)
        action_panel = ctk.CTkFrame(top_frame, width=200, fg_color="transparent")
        action_panel.pack(side='right', fill='y', padx=(0, 5))
        
        self.btn_start = ctk.CTkButton(action_panel, text='开始注册', command=self.start_registration,
                                     font=("Arial", 18, "bold"), height=60, width=160, fg_color="#2cc985", hover_color="#25a870")
        self.btn_start.pack(pady=(20, 10))
        
        self.btn_stop = ctk.CTkButton(action_panel, text='停止注册', command=self.stop_registration,
                                    font=("Arial", 18, "bold"), height=60, width=160, fg_color="#ff4d4d", hover_color="#d63030", state="disabled")
        self.btn_stop.pack(pady=10)

        # Other utility buttons (moved to Action Panel bottom)
        util_frame = ctk.CTkFrame(action_panel, fg_color="transparent")
        util_frame.pack(side='bottom', pady=20)
        
        ctk.CTkButton(util_frame, text='保存参数', command=self.save_config, width=160, height=32).pack(pady=4)
        ctk.CTkButton(util_frame, text='恢复默认', command=self.reset_defaults, width=160, height=32).pack(pady=4)
        ctk.CTkButton(util_frame, text='一键打包', command=self.build_package, width=160, height=32).pack(pady=4)


        # --- Middle Section: Config Parameters (2 Columns) ---
        config_frame = ctk.CTkFrame(parent)
        config_frame.pack(fill='x', padx=10, pady=5)
        
        ctk.CTkLabel(config_frame, text="参数配置", font=("Arial", 16, "bold")).pack(anchor='w', padx=15, pady=(10, 5))
        
        grid_frame = ctk.CTkFrame(config_frame, fg_color="transparent")
        grid_frame.pack(fill='x', padx=10, pady=(0, 10))
        
        # Configure Grid (2 columns)
        grid_frame.grid_columnconfigure(0, weight=1)
        grid_frame.grid_columnconfigure(1, weight=1)
        
        # Helper to create rows
        def add_config_row(parent, row, col, label_text, widget_func):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.grid(row=row, column=col, sticky="ew", padx=10, pady=4)
            ctk.CTkLabel(f, text=label_text, width=80, anchor='e').pack(side='left', padx=(0, 10))
            widget = widget_func(f)
            widget.pack(side='left', fill='x', expand=True)
            return widget

        # --- Left Column (Col 0) ---
        # 1. BitBrowser URL
        add_config_row(grid_frame, 0, 0, "比特地址:", lambda p: ctk.CTkEntry(p, textvariable=self.bit_url_var))
        
        # 2. BitBrowser Secret
        add_config_row(grid_frame, 1, 0, "比特密钥:", lambda p: ctk.CTkEntry(p, textvariable=self.bit_secret_var, show='*'))
        
        # 3. Platform URL
        add_config_row(grid_frame, 2, 0, "平台地址:", lambda p: ctk.CTkEntry(p, textvariable=self.platform_url_var))
        
        # 4. Concurrency
        def create_concurrent(p):
            f = ctk.CTkFrame(p, fg_color="transparent")
            self.concurrent_sel_var = tkinter.StringVar(value=str(self.concurrent_var.get()))
            cb = ctk.CTkComboBox(f, values=[str(i) for i in range(1,21)], variable=self.concurrent_sel_var, 
                               command=lambda v: self.concurrent_var.set(int(v)), width=100)
            cb.pack(side='left')
            return f
        add_config_row(grid_frame, 3, 0, "并发数量:", create_concurrent)
        
        # 5. XPath File
        def create_xpath(p):
            f = ctk.CTkFrame(p, fg_color="transparent")
            ctk.CTkCheckBox(f, text="启用", variable=self.enable_xpath_var, width=60).pack(side='left')
            ctk.CTkEntry(f, textvariable=self.xpath_var).pack(side='left', fill='x', expand=True, padx=5)
            ctk.CTkButton(f, text="...", width=30, command=self.choose_xpath).pack(side='left')
            return f
        add_config_row(grid_frame, 4, 0, "自动化:", create_xpath)

        # --- Right Column (Col 1) ---
        # 1. Timeout / Poll
        def create_timeout(p):
            f = ctk.CTkFrame(p, fg_color="transparent")
            ctk.CTkEntry(f, textvariable=self.timeout_ms_var, width=70).pack(side='left')
            ctk.CTkLabel(f, text="ms (轮询").pack(side='left', padx=2)
            
            self.poll_sel_var = tkinter.StringVar(value=str(self.poll_ms_var.get()))
            ctk.CTkComboBox(f, values=['300','500','700'], variable=self.poll_sel_var, 
                          command=lambda v: self.poll_ms_var.set(int(v)), width=70).pack(side='left', padx=2)
            ctk.CTkLabel(f, text="ms)").pack(side='left')
            return f
        add_config_row(grid_frame, 0, 1, "超时设置:", create_timeout)
        
        # 2. Max Rounds
        add_config_row(grid_frame, 1, 1, "最大轮数:", lambda p: ctk.CTkEntry(p, textvariable=self.max_rounds_var))
        
        # 3. Build Output
        def create_build(p):
            f = ctk.CTkFrame(p, fg_color="transparent")
            ctk.CTkEntry(f, textvariable=self.build_output_var).pack(side='left', fill='x', expand=True, padx=(0,5))
            ctk.CTkButton(f, text="...", width=30, command=self.choose_build_output).pack(side='left')
            return f
        add_config_row(grid_frame, 2, 1, "输出目录:", create_build)
        
        # 4. Config Path
        def create_conf(p):
            f = ctk.CTkFrame(p, fg_color="transparent")
            ctk.CTkEntry(f, textvariable=self.config_path_var).pack(side='left', fill='x', expand=True, padx=(0,5))
            # Import/Export buttons
            ctk.CTkButton(f, text="导入", width=40, command=self.import_config).pack(side='left', padx=2)
            ctk.CTkButton(f, text="导出", width=40, command=self.export_config).pack(side='left', padx=2)
            return f
        add_config_row(grid_frame, 3, 1, "配置文件:", create_conf)

        # --- Bottom Section: Logs ---
        log_frame = ctk.CTkFrame(parent)
        log_frame.pack(fill='both', expand=True, padx=10, pady=(5, 10))
        ctk.CTkLabel(log_frame, text="运行日志", font=("Arial", 14, "bold")).pack(anchor='w', padx=10, pady=2)
        
        self.log = ctk.CTkTextbox(log_frame, height=150)
        self.log.pack(fill='both', expand=True, padx=5, pady=5)

    def _build_ip_pool_tab(self, parent):
        # Config Path
        conf_frame = ctk.CTkFrame(parent)
        conf_frame.pack(fill='x', padx=12, pady=8)
        ctk.CTkLabel(conf_frame, text="IP池配置路径:").pack(side='left', padx=6)
        ctk.CTkEntry(conf_frame, textvariable=self.ip_config_path_var, width=500).pack(side='left', padx=6)
        ctk.CTkButton(conf_frame, text="浏览", command=self.choose_ip_config_path, width=80).pack(side='left', padx=6)
        ctk.CTkButton(conf_frame, text="重新加载", command=self.reload_ip_config, width=80).pack(side='left', padx=6)

        # Settings Panel (Max Usage & Defaults)
        settings_frame = ctk.CTkFrame(parent)
        settings_frame.pack(fill='x', padx=12, pady=8)
        
        # Row 1: Max Usage
        row1 = ctk.CTkFrame(settings_frame, fg_color="transparent")
        row1.pack(fill='x', padx=6, pady=6)
        ctk.CTkLabel(row1, text="单个IP最大使用次数 (1-999):").pack(side='left', padx=6)
        ctk.CTkEntry(row1, textvariable=self.ip_max_usage_var, width=100).pack(side='left', padx=6)
        ctk.CTkButton(row1, text="应用设置", command=self.apply_ip_settings, width=100).pack(side='left', padx=6)
        
        # Row 2: Default IP Params
        row2 = ctk.CTkFrame(settings_frame, fg_color="transparent")
        row2.pack(fill='x', padx=6, pady=6)
        ctk.CTkLabel(row2, text="默认端口:").pack(side='left', padx=6)
        ctk.CTkEntry(row2, textvariable=self.ip_def_port_var, width=80).pack(side='left', padx=6)
        ctk.CTkLabel(row2, text="默认用户:").pack(side='left', padx=6)
        ctk.CTkEntry(row2, textvariable=self.ip_def_user_var, width=120).pack(side='left', padx=6)
        ctk.CTkLabel(row2, text="默认密码:").pack(side='left', padx=6)
        ctk.CTkEntry(row2, textvariable=self.ip_def_pass_var, width=120).pack(side='left', padx=6)
        ctk.CTkLabel(row2, text="协议:").pack(side='left', padx=6)
        ctk.CTkComboBox(row2, values=['socks5', 'http', 'https'], variable=self.ip_def_protocol_var, width=100).pack(side='left', padx=6)
        ctk.CTkLabel(row2, text="(仅在导入只有IP地址的数据时生效)", text_color="gray").pack(side='left', padx=6)

        # Stats Dashboard
        stats_frame = ctk.CTkFrame(parent)
        stats_frame.pack(fill='x', padx=12, pady=8)
        self.stat_total_label = ctk.CTkLabel(stats_frame, text="总IP数: 0")
        self.stat_total_label.pack(side='left', padx=20)
        self.stat_avail_label = ctk.CTkLabel(stats_frame, text="可用IP数: 0")
        self.stat_avail_label.pack(side='left', padx=20)
        self.stat_used_label = ctk.CTkLabel(stats_frame, text="已用邮箱数: 0")
        self.stat_used_label.pack(side='left', padx=20)

        # Batch Operations
        batch_frame = ctk.CTkFrame(parent)
        batch_frame.pack(fill='x', padx=12, pady=8)
        ctk.CTkLabel(batch_frame, text="批量操作:").pack(side='left', padx=6)
        ctk.CTkButton(batch_frame, text="导入IP (TXT/CSV)", command=self.import_ips_dialog).pack(side='left', padx=6)
        ctk.CTkButton(batch_frame, text="粘贴导入IP", command=self.paste_import_ips_dialog).pack(side='left', padx=6)
        ctk.CTkButton(batch_frame, text="删除IP (正则)", command=self.delete_ips_dialog).pack(side='left', padx=6)
        ctk.CTkButton(batch_frame, text="清空所有IP", command=self.clear_ips, fg_color="red").pack(side='left', padx=6)
        ctk.CTkButton(batch_frame, text="导出当前IP池", command=self.export_ips).pack(side='left', padx=6)

        # IP List View (Treeview)
        list_frame = ctk.CTkFrame(parent)
        list_frame.pack(fill='both', expand=True, padx=12, pady=8)
        
        from tkinter import ttk
        columns = ("host", "port", "user", "pass", "protocol", "status")
        self.ip_tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended")
        self.ip_tree.heading("host", text="Host/IP")
        self.ip_tree.heading("port", text="Port")
        self.ip_tree.heading("user", text="User")
        self.ip_tree.heading("pass", text="Password")
        self.ip_tree.heading("protocol", text="Protocol")
        self.ip_tree.heading("status", text="使用状态")
        
        self.ip_tree.column("host", width=150)
        self.ip_tree.column("port", width=80)
        self.ip_tree.column("user", width=100)
        self.ip_tree.column("pass", width=100)
        self.ip_tree.column("protocol", width=80)
        self.ip_tree.column("status", width=120)
        
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.ip_tree.yview)
        hsb = ttk.Scrollbar(list_frame, orient="horizontal", command=self.ip_tree.xview)
        self.ip_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        self.ip_tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

    def choose_ip_config_path(self):
        p = filedialog.askopenfilename(filetypes=[('JSON','*.json'), ('All','*')])
        if p:
            self.ip_config_path_var.set(p)
            self.ip_manager = IPManager(p)
            self.reload_ip_config()

    def reload_ip_config(self):
        self.ip_manager.config_path = self.ip_config_path_var.get()
        self.ip_manager._load_config()
        self.ip_max_usage_var.set(self.ip_manager.get_max_usage())
        self.refresh_ip_stats()
        self.refresh_ip_list()
        self.trigger_refresh(force=True)
        self.append_log(f"已加载IP池配置: {self.ip_manager.config_path}")

    def apply_ip_settings(self):
        try:
            val = self.ip_max_usage_var.get()
            if val < 1: val = 1
            if val > 999: val = 999
            self.ip_manager.set_max_usage(val)
            self.refresh_ip_stats()
            self.append_log(f"已更新最大使用次数为: {val}")
        except Exception as e:
            messagebox.showerror("错误", f"设置失败: {e}")

    def refresh_ip_stats(self):
        stats = self.ip_manager.get_stats()
        self.stat_total_label.configure(text=f"总IP数: {stats['total_ips']}")
        self.stat_avail_label.configure(text=f"可用IP数: {stats['available_ips']}")
        self.stat_used_label.configure(text=f"已用邮箱数: {stats['used_emails_count']}")
        
        self.refresh_ip_list()

    def refresh_ip_list(self):
        # Clear
        for item in self.ip_tree.get_children():
            self.ip_tree.delete(item)
            
        all_ips = self.ip_manager.get_all_ips()
        max_u = self.ip_manager.get_max_usage()
        
        # Insert
        # Show only first 500 to avoid UI lag if too many
        limit = 500
        for i, ip in enumerate(all_ips):
            if i >= limit:
                break
            
            used_count = len(ip.get('used_by', []))
            status_text = f"已用: {used_count}/{max_u}"
            
            # Simple color logic? Treeview doesn't support easy row colors without tags.
            # We can use tags.
            
            self.ip_tree.insert("", "end", values=(
                ip['host'], 
                ip['port'], 
                ip.get('proxyUserName',''), 
                ip.get('proxyPassword',''),
                ip.get('protocol', 'socks5'),
                status_text
            ))

    def import_ips_dialog(self):
        # Simple dialog to paste or load file
        p = filedialog.askopenfilename(filetypes=[('Text/CSV','*.txt *.csv'), ('All','*')])
        if p:
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                count = self.ip_manager.import_ips(
                    content,
                    default_port=self.ip_def_port_var.get().strip(),
                    default_user=self.ip_def_user_var.get().strip(),
                    default_pass=self.ip_def_pass_var.get().strip(),
                    default_protocol=self.ip_def_protocol_var.get().strip()
                )
                self.reload_ip_config() # This refreshes stats and list
                messagebox.showinfo("导入成功", f"成功导入 {count} 个新IP")
                self.append_log(f"导入 {count} 个IP来自 {p}")
            except Exception as e:
                messagebox.showerror("导入失败", str(e))

    def paste_import_ips_dialog(self):
        # Using a Toplevel with a Text widget for pasting large content
        top = ctk.CTkToplevel(self)
        top.title("粘贴导入IP")
        top.geometry("650x500")
        top.attributes('-topmost', True) # Keep on top for focus
        
        # Main container with consistent padding and rounded corners
        main_frame = ctk.CTkFrame(top, corner_radius=10)
        main_frame.pack(fill='both', expand=True, padx=15, pady=15)
        
        # Header
        header_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        header_frame.pack(fill='x', padx=10, pady=(10, 5))
        ctk.CTkLabel(header_frame, text="批量导入IP", font=("Arial", 18, "bold")).pack(side='left')
        
        # Instruction
        ctk.CTkLabel(main_frame, text="请粘贴IP列表 (格式: host:port:user:pass 或 host ...)", 
                   font=("Arial", 14), text_color="gray").pack(anchor='w', padx=15, pady=(5, 5))
        
        # Text Area with border effect
        text_container = ctk.CTkFrame(main_frame, fg_color="transparent")
        text_container.pack(fill='both', expand=True, padx=10, pady=5)
        
        text_area = ctk.CTkTextbox(text_container, font=("Arial", 13), border_width=2, corner_radius=6)
        text_area.pack(fill='both', expand=True)
        text_area.focus_set()
        
        def do_import():
            content = text_area.get("1.0", "end").strip()
            if not content:
                return
            
            try:
                count = self.ip_manager.import_ips(
                    content,
                    default_port=self.ip_def_port_var.get().strip(),
                    default_user=self.ip_def_user_var.get().strip(),
                    default_pass=self.ip_def_pass_var.get().strip(),
                    default_protocol=self.ip_def_protocol_var.get().strip()
                )
                self.reload_ip_config()
                messagebox.showinfo("导入成功", f"成功导入 {count} 个新IP")
                self.append_log(f"粘贴导入 {count} 个IP")
                top.destroy()
            except Exception as e:
                messagebox.showerror("导入失败", str(e))
                
        # Action Buttons
        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(fill='x', padx=10, pady=15)
        
        ctk.CTkButton(btn_frame, text="取消", command=top.destroy, 
                    fg_color="transparent", border_width=1, text_color=("gray10", "gray90"), 
                    width=100, height=36).pack(side='right', padx=5)
                    
        ctk.CTkButton(btn_frame, text="确定导入", command=do_import, 
                    width=120, height=36, font=("Arial", 14, "bold")).pack(side='right', padx=5)

                
    def delete_ips_dialog(self):
        dialog = ctk.CTkInputDialog(text="输入要删除的IP正则 (匹配host或port):", title="批量删除IP")
        pattern = dialog.get_input()
        if pattern:
            count = self.ip_manager.delete_ips(pattern)
            self.refresh_ip_stats()
            messagebox.showinfo("完成", f"已删除 {count} 个IP")
            self.append_log(f"已删除 {count} 个IP (正则: {pattern})")

    def clear_ips(self):
        if messagebox.askyesno("确认", "确定要清空所有IP吗？此操作不可恢复。"):
            self.ip_manager.clear_ips()
            self.refresh_ip_stats()
            self.append_log("已清空IP池")

    def export_ips(self):
        p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if p:
            try:
                ips = self.ip_manager.get_all_ips()
                with open(p, 'w', encoding='utf-8') as f:
                    f.write("host,port,user,pass,usage\n")
                    for ip in ips:
                        f.write(f"{ip['host']},{ip['port']},{ip.get('proxyUserName','')},{ip.get('proxyPassword','')},{ip.get('usage_count',0)}\n")
                messagebox.showinfo("导出成功", f"已导出 {len(ips)} 个IP")
            except Exception as e:
                messagebox.showerror("导出失败", str(e))

    def choose_csv(self):
        p = filedialog.askopenfilename(filetypes=[('CSV','*.csv'), ('All','*')])
        if p:
            self.csv_var.set(p)

    def choose_xpath(self):
        p = filedialog.askopenfilename(filetypes=[('JSON','*.json'), ('All','*')])
        if p:
            self.xpath_var.set(p)

    def choose_build_output(self):
        p = filedialog.askdirectory()
        if p:
            self.build_output_var.set(p)

    def choose_config_path(self):
        p = filedialog.askopenfilename(filetypes=[('JSON','*.json'), ('All','*')])
        if p:
            self.config_path_var.set(p)

    def load_config_manual(self):
        self._load_config()
        self.append_log(f"已从 {self.config_path_var.get()} 加载参数")

    def load_csv_preview(self):
        try:
            with open(self.csv_var.get(), 'r', encoding='utf-8') as f:
                txt = f.read(2000)
        except Exception:
            try:
                with open(self.csv_var.get(), 'r', encoding='utf-8-sig') as f:
                    txt = f.read(2000)
            except Exception as e:
                txt = str(e)
        self.preview.delete('1.0', 'end')
        self.preview.insert('end', txt)
        rows = read_rows(self.csv_var.get())
        total = len(rows)
        succ = sum(1 for r in rows if str(r.get('status','')).strip() == 'good')
        fail = sum(1 for r in rows if str(r.get('status','')).strip() == 'fail')
        self.cnt_total_var.set(str(total))
        self.cnt_success_var.set(str(succ))
        self.cnt_fail_var.set(str(fail))

    def append_log(self, s: str):
        self.log.insert('end', s + '\n')
        self.log.see('end')
        try:
            print(s)
        except Exception:
            pass

    def progress_cb(self, data):
        self.cnt_total_var.set(str(data.get('total',0)))
        self.cnt_success_var.set(str(data.get('success',0)))
        self.cnt_fail_var.set(str(data.get('fail',0)))

    def _build_email_pool_tab(self):
        parent = self.tab_email
        
        # 1. Registration Task Management (Import/Export/Preview)
        # 3. Email Pool Functionality (Import with auto-url)
        
        # Tools Bar
        tool_frame = ctk.CTkFrame(parent)
        tool_frame.pack(fill='x', padx=12, pady=8)
        
        ctk.CTkButton(tool_frame, text="导入账号 (TXT/CSV)", command=self.import_emails_dialog).pack(side='left', padx=6)
        ctk.CTkButton(tool_frame, text="粘贴导入 (自动生成URL)", command=self.paste_import_emails_dialog).pack(side='left', padx=6)
        ctk.CTkButton(tool_frame, text="导出为注册任务CSV", command=self.export_emails_to_csv).pack(side='left', padx=6)
        ctk.CTkButton(tool_frame, text="刷新列表", command=self.refresh_email_list).pack(side='left', padx=6)
        
        # Add Clear Button
        ctk.CTkButton(tool_frame, text="清空邮箱", command=self.clear_emails_dialog, fg_color="#ff4d4d", hover_color="#d63030").pack(side='right', padx=6)
        
        # Search Bar
        search_frame = ctk.CTkFrame(parent)
        search_frame.pack(fill='x', padx=12, pady=8)
        ctk.CTkLabel(search_frame, text="查找邮箱:").pack(side='left', padx=6)
        self.email_search_var = tkinter.StringVar()
        self.email_search_entry = ctk.CTkEntry(search_frame, textvariable=self.email_search_var, width=300, placeholder_text="输入邮箱地址...")
        self.email_search_entry.pack(side='left', padx=6)
        ctk.CTkButton(search_frame, text="查找/获取验证码地址", command=self.search_email_url).pack(side='left', padx=6)
        ctk.CTkButton(search_frame, text="删除选中", command=self.delete_selected_email, fg_color="red").pack(side='left', padx=6)
        
        # Data Preview (Table-like using Textbox for now, or Treeview if possible)
        # CustomTkinter doesn't have a Grid/Table widget natively. Using Treeview within a Frame.
        
        list_frame = ctk.CTkFrame(parent)
        list_frame.pack(fill='both', expand=True, padx=12, pady=8)
        
        # Treeview for Emails
        from tkinter import ttk
        
        columns = ("email", "password", "code_url", "status")
        self.email_tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended")
        self.email_tree.heading("email", text="邮箱账号")
        self.email_tree.heading("password", text="密码")
        self.email_tree.heading("code_url", text="验证码接收地址")
        self.email_tree.heading("status", text="使用状态")
        
        self.email_tree.column("email", width=200)
        self.email_tree.column("password", width=120)
        self.email_tree.column("code_url", width=300)
        self.email_tree.column("status", width=80)
        
        # Scrollbars
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.email_tree.yview)
        hsb = ttk.Scrollbar(list_frame, orient="horizontal", command=self.email_tree.xview)
        self.email_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        self.email_tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)
        
        self.refresh_email_list()

    def refresh_home_stats(self):
        # 1. Check System Resources (CPU/Memory)
        try:
            now = time.time()
            if now - self.last_refresh_time > self.resource_check_interval / 1000:
                cpu_usage = psutil.cpu_percent()
                mem_usage = psutil.virtual_memory().percent
                
                # Auto-switch to manual mode if load is high
                if cpu_usage > 70 or mem_usage > 80:
                    if not self.manual_refresh_mode:
                        self.manual_refresh_mode = True
                        self.btn_manual_refresh.pack(side='right', padx=5) # Show refresh button in res_frame if we used pack there? 
                        # Actually we used place.
                        self.btn_manual_refresh.place(relx=0.95, rely=0.05, anchor='ne')
                        self.append_log("系统负载过高，已切换至手动刷新统计模式")
                else:
                    if self.manual_refresh_mode:
                        self.manual_refresh_mode = False
                        self.btn_manual_refresh.place_forget()
                        self.append_log("系统负载正常，恢复自动刷新统计")
                
                self.last_refresh_time = now
        except Exception:
            pass

        # 2. Decide whether to refresh stats
        if not self.manual_refresh_mode:
             self.trigger_refresh()
             
        # Schedule next check
        self.after(1000, self.refresh_home_stats)

    def trigger_refresh(self, force=False):
        # Debounce mechanism
        if self.debounce_timer:
            self.after_cancel(self.debounce_timer)
        
        self.debounce_timer = self.after(300, lambda: self._do_refresh_stats(force))

    def _do_refresh_stats(self, force=False):
        start_time = time.time()
        
        try:
            # Skip if not forced and in manual mode
            if self.manual_refresh_mode and not force:
                return

            # IP Stats
            ip_stats = self.ip_manager.get_stats()
            remaining_usage = ip_stats.get('remaining_usage_count', 0)
            if hasattr(self, 'home_ip_stat_label'):
                self.home_ip_stat_label.configure(text=f"{remaining_usage}")
            
            # Email Stats
            email_stats = self.email_manager.get_stats()
            total_emails = email_stats.get('total_emails', 0)
            used_emails_count = ip_stats.get('used_emails_count', 0)
            available_emails = max(0, total_emails - used_emails_count)
            
            if hasattr(self, 'home_email_stat_label'):
                self.home_email_stat_label.configure(text=f"{available_emails}")
            
            max_reg = min(remaining_usage, available_emails)
            self.max_reg_count_var.set(str(max_reg))
            
            # Update Realtime Counter Display if not running (if running, it's updated by callback)
            if not (self.worker and self.worker.is_alive()):
                try:
                    target = int(self.target_reg_count_var.get())
                except:
                    target = 0
                success = self.cnt_success_var.get()
                
                # Update new labels if they exist
                if hasattr(self, 'lbl_prog_success'):
                    self.lbl_prog_success.configure(text=success)
                if hasattr(self, 'lbl_prog_target'):
                    # Target is bound to variable, so auto-updates, but let's ensure
                    pass 
                if hasattr(self, 'lbl_prog_max'):
                    # Max is bound to variable
                    pass
            
            # Log performance
            duration = (time.time() - start_time) * 1000
            self.refresh_stats_perf.append(duration)
            if len(self.refresh_stats_perf) > 50:
                self.refresh_stats_perf.pop(0)
                
        except Exception:
            pass

    def import_emails_dialog(self):
        p = filedialog.askopenfilename(filetypes=[('Text/CSV','*.txt *.csv'), ('All','*')])
        if p:
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    content = f.read()
                count = self.email_manager.import_emails(content)
                self.refresh_email_list()
                self.trigger_refresh(force=True)
                messagebox.showinfo("导入成功", f"成功导入 {count} 个新邮箱")
                self.append_log(f"导入 {count} 个邮箱来自 {p}")
            except Exception as e:
                messagebox.showerror("导入失败", str(e))

    def paste_import_emails_dialog(self):
        # Improved dialog with Toplevel
        top = ctk.CTkToplevel(self)
        top.title("粘贴导入邮箱")
        # Reduced height by 30% from 500 -> 350
        top.geometry("650x350")
        top.attributes('-topmost', True)
        
        # Main container with consistent padding and rounded corners
        main_frame = ctk.CTkFrame(top, corner_radius=10)
        main_frame.pack(fill='both', expand=True, padx=15, pady=15)
        
        # Header
        header_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        header_frame.pack(fill='x', padx=10, pady=(10, 5))
        ctk.CTkLabel(header_frame, text="批量导入邮箱", font=("Arial", 18, "bold")).pack(side='left')
        
        # Instruction
        ctk.CTkLabel(main_frame, text="请粘贴邮箱账号 (格式: 账号 密码 或 账号\\t密码):", 
                   font=("Arial", 14), text_color="gray").pack(anchor='w', padx=15, pady=(5, 5))
        
        # Text Area with border effect
        text_container = ctk.CTkFrame(main_frame, fg_color="transparent")
        text_container.pack(fill='both', expand=True, padx=10, pady=5)
        
        text_area = ctk.CTkTextbox(text_container, font=("Arial", 13), border_width=2, corner_radius=6)
        text_area.pack(fill='both', expand=True)
        text_area.focus_set()
        
        def do_import():
            content = text_area.get("1.0", "end").strip()
            if not content:
                return
            try:
                count = self.email_manager.import_emails(content)
                self.refresh_email_list()
                self.trigger_refresh() # Update stats
                messagebox.showinfo("导入成功", f"成功导入 {count} 个新邮箱")
                self.append_log(f"粘贴导入 {count} 个邮箱")
                top.destroy()
            except Exception as e:
                messagebox.showerror("导入失败", str(e))
            
        # Action Buttons
        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(fill='x', padx=10, pady=15)
        
        ctk.CTkButton(btn_frame, text="取消", command=top.destroy, 
                    fg_color="transparent", border_width=1, text_color=("gray10", "gray90"), 
                    width=100, height=36).pack(side='right', padx=5)
        
        ctk.CTkButton(btn_frame, text="确定导入", command=do_import, 
                    width=120, height=36, font=("Arial", 14, "bold")).pack(side='right', padx=5)

    def export_emails_to_csv(self):
        # Exports to the main registration task CSV format
        # Format required by register_kling_bitbrowser: email, (password?), ...
        # Actually register script reads keys: 'email', '账号', etc.
        p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[('CSV','*.csv')])
        if p:
            try:
                import csv
                emails = self.email_manager.get_all_emails()
                with open(p, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    # Write header
                    writer.writerow(['email', 'password', 'code_url', 'status'])
                    for e in emails:
                        writer.writerow([e['email'], e['password'], e['code_url'], ''])
                messagebox.showinfo("导出成功", f"已导出 {len(emails)} 个邮箱到 {p}")
                
                # Optionally update the main CSV path
                if messagebox.askyesno("提示", "是否将此文件设置为当前注册任务文件？"):
                    self.csv_var.set(p)
                    self.append_log(f"已更新注册任务文件为: {p}")
                    
            except Exception as e:
                messagebox.showerror("导出失败", str(e))

    def refresh_email_list(self):
        # Clear
        for item in self.email_tree.get_children():
            self.email_tree.delete(item)
            
        # Load
        emails = self.email_manager.get_all_emails()
        used_emails = set(self.ip_manager.get_used_emails()) # Optimize with set for lookup
        
        # Filter if search
        query = self.email_search_var.get().strip().lower()
        
        limit = 500 # Virtual scrolling limit
        count = 0
        
        for e in emails:
            if count >= limit:
                break
                
            if query and query not in e['email'].lower():
                continue
            
            status = "可用"
            if e['email'] in used_emails:
                status = "已用"
                
            self.email_tree.insert("", "end", values=(e['email'], e['password'], e['code_url'], status))
            count += 1
            
    def clear_emails_dialog(self):
        if messagebox.askyesno("确认清空", "确定要清空所有邮箱数据吗？此操作不可撤销！"):
            self.email_manager.clear_emails()
            self.refresh_email_list()
            self.trigger_refresh() # Update stats
            self.append_log("已清空所有邮箱数据")


    def search_email_url(self):
        query = self.email_search_var.get().strip()
        if not query:
            messagebox.showwarning("提示", "请输入邮箱地址")
            return
        
        url = self.email_manager.get_code_url(query)
        if url:
            # Show in dialog
            self.refresh_email_list() # Filter view
            
            # Also show popup
            top = ctk.CTkToplevel(self)
            top.title("验证码接收地址")
            top.geometry("600x150")
            
            ctk.CTkLabel(top, text=f"邮箱: {query}").pack(pady=10)
            entry = ctk.CTkEntry(top, width=500)
            entry.pack(pady=5)
            entry.insert(0, url)
            entry.configure(state='readonly')
            
            def copy():
                self.clipboard_clear()
                self.clipboard_append(url)
                top.destroy()
                
            ctk.CTkButton(top, text="复制并关闭", command=copy).pack(pady=10)
            
        else:
            messagebox.showerror("未找到", f"邮箱池中未找到: {query}")

    def delete_selected_email(self):
        selected = self.email_tree.selection()
        if not selected:
            return
        
        if not messagebox.askyesno("确认", f"确定要删除选中的 {len(selected)} 个邮箱吗？"):
            return
            
        count = 0
        for item in selected:
            vals = self.email_tree.item(item)['values']
            if vals:
                email = vals[0] # email is first column
                self.email_manager.delete_email(email)
                count += 1
        
        self.refresh_email_list()
        self.refresh_home_stats()
        self.append_log(f"已删除 {count} 个邮箱")

    def start_registration(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.enable_xpath_var.get():
            self.append_log('未启用自动化注册')
            return
            
        # Validate Target Count
        try:
            target_count = int(self.target_reg_count_var.get())
            if target_count <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "请输入有效的注册数量 (正整数)")
            return
            
        try:
            max_reg = int(self.max_reg_count_var.get())
        except:
            max_reg = 0
            
        if target_count > max_reg:
            messagebox.showwarning("数量不足", f"IP或邮箱数量不足\n当前最多可注册 {max_reg} 个账号")
            return
            
        # Prepare task data from Email Manager
        emails = self.email_manager.get_all_emails()
        used_emails = self.ip_manager.get_used_emails()
        valid_rows = []
        
        for e in emails:
            if e['email'] in used_emails:
                continue
            valid_rows.append([e['email'], e.get('password',''), e.get('code_url','')])
            
        if not valid_rows:
            messagebox.showwarning("提示", "没有可用的邮箱账号 (所有账号均已使用)")
            return
            
        # Create temp CSV
        temp_csv = os.path.join(self.exe_dir, 'temp_registration_tasks.csv')
        try:
            with open(temp_csv, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['email', 'password', 'code_url'])
                writer.writerows(valid_rows)
        except Exception as e:
            messagebox.showerror("错误", f"创建临时任务文件失败: {e}")
            return
            
        self.stop_event.clear()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        
        # Reset counters
        self.cnt_total_var.set('0')
        self.cnt_success_var.set('0')
        self.cnt_fail_var.set('0')
        
        args = (
            temp_csv,
            self.xpath_var.get(),
            self.platform_url_var.get(),
            self.bit_url_var.get(),
            self.bit_secret_var.get() or None,
            int(self.concurrent_var.get()),
            int(self.timeout_ms_var.get()),
            int(self.poll_ms_var.get()),
            int(self.max_rounds_var.get())
        )
        
        def run():
            try:
                self.append_log(f"任务开始... 目标注册: {target_count}")
                
                def progress(total, success, fail):
                    self.cnt_total_var.set(str(total))
                    self.cnt_success_var.set(str(success))
                    self.cnt_fail_var.set(str(fail))
                    # Update Realtime Counter
                    try:
                        # Update new labels
                        if hasattr(self, 'lbl_prog_success'):
                            self.lbl_prog_success.configure(text=str(success))
                    except:
                        pass
                
                def exhaustion(reason, stats):
                    # Callback when IP exhausted
                    return messagebox.askretrycancel("资源耗尽", f"IP资源不足 ({stats['remaining_usage_count']})，是否重试？") and 'retry' or 'cancel'

                run_batch(
                    *args, 
                    logger=self.append_log, 
                    stop_event=self.stop_event,
                    progress_cb=progress,
                    ip_manager=self.ip_manager,
                    exhaustion_cb=exhaustion,
                    target_success_count=target_count,
                    email_manager=self.email_manager
                )
                self.append_log("任务结束")
            except Exception as e:
                self.append_log(f"任务异常: {e}")
                import traceback
                traceback.print_exc()
            finally:
                self.btn_start.configure(state="normal")
                self.btn_stop.configure(state="disabled")
                self.worker = None
                try:
                    if os.path.exists(temp_csv):
                        os.remove(temp_csv)
                except:
                    pass

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def stop_registration(self):
        if not self.worker or not self.worker.is_alive():
            return
            
        if messagebox.askyesno("确认", "确定要停止注册任务吗？"):
            self.stop_event.set()
            self.btn_stop.configure(state="disabled")
            self.append_log("正在停止任务...")

    def import_config(self):
        p = filedialog.askopenfilename(filetypes=[('JSON','*.json')])
        if p:
            self.config_path_var.set(p)
            self._load_config()
            self.append_log(f"已导入配置: {p}")

    def export_config(self):
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[('JSON','*.json')])
        if p:
            # Save current to p
            old_path = self.config_path_var.get()
            self.config_path_var.set(p)
            self.save_config()
            self.config_path_var.set(old_path) # Restore? Or keep new? 
            # User likely wants to export a copy, but maybe switch to it?
            # Usually export means save copy. Switch means "Save As". 
            # I'll just save a copy and keep using current config path.
            self.append_log(f"已导出配置到: {p}")

    def choose_config_path(self):
        p = filedialog.askopenfilename(filetypes=[('JSON','*.json')])
        if p:
            self.config_path_var.set(p)
            self._load_config()

    def save_config(self):
        # Resolve paths relative to exe_dir if possible
        def make_rel(p):
            if p and os.path.isabs(p) and p.startswith(self.exe_dir):
                return os.path.relpath(p, self.exe_dir)
            return p

        cfg = {
            'xpaths': make_rel(self.xpath_var.get()),
            'bit_url': self.bit_url_var.get(),
            'bit_secret': self.bit_secret_var.get(),
            'platform_url': self.platform_url_var.get(),
            'concurrency': int(self.concurrent_var.get()),
            'timeout_ms': int(self.timeout_ms_var.get()),
            'poll_ms': int(self.poll_ms_var.get()),
            'max_rounds': int(self.max_rounds_var.get()),
            'build_output': make_rel(self.build_output_var.get()),
            # IP Defaults
            'ip_def_port': self.ip_def_port_var.get(),
            'ip_def_user': self.ip_def_user_var.get(),
            'ip_def_pass': self.ip_def_pass_var.get(),
            'ip_def_protocol': self.ip_def_protocol_var.get(),
            'ip_config_path': make_rel(self.ip_config_path_var.get()),
        }
        try:
            with open(self.config_path_var.get(), 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            # self.append_log('参数已保存') # Too noisy for auto-save
        except Exception as e:
            self.append_log(f"保存配置失败: {str(e)}")

    def _load_config(self):
        try:
            if not os.path.exists(self.config_path_var.get()):
                return
                
            with open(self.config_path_var.get(), 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            
            # Helper to resolve relative paths
            def resolve(p):
                if p and not os.path.isabs(p):
                    return os.path.join(self.exe_dir, p)
                return p
            
            # Helper for path recovery
            def recover_path(p, default_name, desc):
                resolved = resolve(p)
                if resolved and os.path.exists(resolved):
                    return resolved
                
                # Try exe_dir
                fallback = os.path.join(self.exe_dir, default_name)
                if os.path.exists(fallback):
                    self.append_log(f"注意: {desc}路径无效，已自动恢复到默认位置: {fallback}")
                    return fallback
                
                # Try base_dir
                fallback_base = os.path.join(self.base_dir, default_name)
                if os.path.exists(fallback_base):
                    self.append_log(f"注意: {desc}路径无效，已自动恢复到开发环境位置: {fallback_base}")
                    return fallback_base
                
                # Still missing? Return fallback but log warning
                self.append_log(f"警告: 无法找到{desc} ({default_name})，将使用默认路径")
                return resolved if resolved else fallback

            self.xpath_var.set(recover_path(cfg.get('xpaths'), 'kling_xpaths.json', "XPath配置"))
            self.bit_url_var.set(cfg.get('bit_url', self.bit_url_var.get()))
            self.bit_secret_var.set(cfg.get('bit_secret', self.bit_secret_var.get()))
            self.platform_url_var.set(cfg.get('platform_url', self.platform_url_var.get()))
            self.concurrent_var.set(int(cfg.get('concurrency', self.concurrent_var.get())))
            self.timeout_ms_var.set(int(cfg.get('timeout_ms', self.timeout_ms_var.get())))
            self.poll_ms_var.set(int(cfg.get('poll_ms', self.poll_ms_var.get())))
            self.max_rounds_var.set(int(cfg.get('max_rounds', self.max_rounds_var.get())))
            self.build_output_var.set(resolve(cfg.get('build_output', self.build_output_var.get())))
            
            # IP Defaults
            self.ip_def_port_var.set(cfg.get('ip_def_port', ''))
            self.ip_def_user_var.set(cfg.get('ip_def_user', ''))
            self.ip_def_pass_var.set(cfg.get('ip_def_pass', ''))
            self.ip_def_protocol_var.set(cfg.get('ip_def_protocol', 'socks5'))
            
            # IP Config Path
            ip_conf_path = recover_path(cfg.get('ip_config_path'), 'resources/ip_pool.json', "IP池配置")
            self.ip_config_path_var.set(ip_conf_path)
            
            # Reload IP Manager if path changed or just to be safe
            if os.path.abspath(ip_conf_path) != os.path.abspath(self.ip_manager.config_path):
                self.ip_manager = IPManager(ip_conf_path)
                self.refresh_ip_stats()
            
            # Update Comboboxes
            self.concurrent_sel_var.set(str(self.concurrent_var.get()))
            self.poll_sel_var.set(str(self.poll_ms_var.get()))
            
            self.append_log('已加载上次保存的参数')
        except Exception:
            pass

    def reset_defaults(self):
        xpath_path = os.path.join(self.exe_dir, 'kling_xpaths.json')
        if not os.path.exists(xpath_path):
            xpath_path = os.path.join(self.base_dir, 'kling_xpaths.json')
        self.xpath_var.set(xpath_path)

        self.bit_url_var.set('http://127.0.0.1:54345')
        self.bit_secret_var.set(os.environ.get('BITBROWSER_SECRET') or '')
        self.platform_url_var.set('https://klingai.com/global')
        self.concurrent_var.set(1)
        self.timeout_ms_var.set(100000)
        self.poll_ms_var.set(500)
        self.max_rounds_var.set(5)
        self.build_output_var.set(os.path.join(self.exe_dir, 'dist'))
        
        self.ip_def_port_var.set('')
        self.ip_def_user_var.set('')
        self.ip_def_pass_var.set('')
        self.ip_def_protocol_var.set('socks5')

    def build_package(self):
        try:
            base_dir = os.path.dirname(__file__)
            env = os.environ.copy()
            env['BUILD_OUT'] = self.build_output_var.get()
            if sys.platform.startswith('win'):
                script = os.path.join(base_dir, 'run_build_windows.bat')
                if not os.path.exists(script):
                    self.append_log('未找到打包脚本: run_build_windows.bat')
                    return
                def run():
                    try:
                        subprocess.run([script], cwd=base_dir, env=env, check=True)
                        self.append_log('打包完成')
                    except Exception as e:
                        self.append_log(str(e))
                threading.Thread(target=run, daemon=True).start()
            else:
                script = os.path.join(base_dir, 'run_build_mac.sh')
                if not os.path.exists(script):
                    self.append_log('未找到打包脚本: run_build_mac.sh')
                    return
                def run():
                    try:
                        subprocess.run(['bash', script], cwd=base_dir, env=env, check=True)
                        self.append_log('打包完成')
                    except Exception as e:
                        self.append_log(str(e))
                threading.Thread(target=run, daemon=True).start()
        except Exception as e:
            self.append_log(str(e))


if __name__ == '__main__':
    App().mainloop()
