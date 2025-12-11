import os
import json
import threading
import tkinter
from tkinter import filedialog
import customtkinter as ctk

from register_kling_bitbrowser import run_batch, read_rows


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('KL-账号批量注册工具')
        self.geometry('1100x800')
        ctk.set_appearance_mode('dark')
        ctk.set_default_color_theme('blue')
        self.csv_var = tkinter.StringVar(value='/Users/jerry/Documents/GitHub/KL_AI/kl-mail.csv')
        self.xpath_var = tkinter.StringVar(value='/Users/jerry/Documents/GitHub/KL_AI/kling_xpaths.json')
        self.bit_url_var = tkinter.StringVar(value='http://127.0.0.1:54345')
        self.bit_secret_var = tkinter.StringVar(value=os.environ.get('BITBROWSER_SECRET') or '')
        self.platform_url_var = tkinter.StringVar(value='https://klingai.com/global')
        self.concurrent_var = tkinter.IntVar(value=1)
        self.timeout_ms_var = tkinter.IntVar(value=100000)
        self.poll_ms_var = tkinter.IntVar(value=500)
        self.enable_xpath_var = tkinter.BooleanVar(value=True)
        self.cnt_total_var = tkinter.StringVar(value='0')
        self.cnt_success_var = tkinter.StringVar(value='0')
        self.cnt_fail_var = tkinter.StringVar(value='0')
        self.stop_event = threading.Event()
        self.worker = None
        self._build_ui()
        self._load_config()

    def _build_ui(self):
        top = ctk.CTkFrame(self)
        top.pack(fill='x', padx=12, pady=8)
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text='CSV文件').grid(row=0, column=0, padx=6, pady=6, sticky='w')
        ctk.CTkEntry(top, textvariable=self.csv_var, width=620).grid(row=0, column=1, padx=6, pady=6, sticky='w')
        ctk.CTkButton(top, text='浏览', command=self.choose_csv, width=80).grid(row=0, column=2, padx=6)
        ctk.CTkButton(top, text='加载CSV', command=self.load_csv_preview, width=100).grid(row=0, column=3, padx=6)
        ctk.CTkLabel(self, text='CSV数据预览').pack(anchor='w', padx=12)
        self.preview = ctk.CTkTextbox(self, height=120, width=920)
        self.preview.pack(fill='x', padx=12, pady=6)

        stat = ctk.CTkFrame(self)
        stat.pack(fill='x', padx=12, pady=4)
        for i in range(6):
            stat.grid_columnconfigure(i, weight=0, uniform='a')
        ctk.CTkLabel(stat, text='账号数量').grid(row=0, column=0, padx=6, sticky='w')
        ctk.CTkLabel(stat, textvariable=self.cnt_total_var).grid(row=0, column=1, padx=6, sticky='w')
        ctk.CTkLabel(stat, text='成功').grid(row=0, column=2, padx=6, sticky='w')
        ctk.CTkLabel(stat, textvariable=self.cnt_success_var).grid(row=0, column=3, padx=6, sticky='w')
        ctk.CTkLabel(stat, text='失败').grid(row=0, column=4, padx=6, sticky='w')
        ctk.CTkLabel(stat, textvariable=self.cnt_fail_var).grid(row=0, column=5, padx=6, sticky='w')

        cfg = ctk.CTkFrame(self)
        cfg.pack(fill='x', padx=12, pady=8)
        for i in range(4):
            cfg.grid_columnconfigure(i, weight=1, uniform='b')
        ctk.CTkLabel(cfg, text='比特浏览器接口').grid(row=0, column=0, padx=6, pady=6, sticky='w')
        ctk.CTkEntry(cfg, textvariable=self.bit_url_var, width=260).grid(row=0, column=1, padx=6, pady=6, sticky='w')
        ctk.CTkLabel(cfg, text='比特浏览器密钥').grid(row=0, column=2, padx=6, pady=6, sticky='w')
        ctk.CTkEntry(cfg, textvariable=self.bit_secret_var, show='*', width=200).grid(row=0, column=3, padx=6, pady=6, sticky='w')
        ctk.CTkLabel(cfg, text='浏览器模式').grid(row=1, column=0, padx=6, pady=6, sticky='w')
        ctk.CTkComboBox(cfg, values=['bitbrowser'], width=120).grid(row=1, column=1, padx=6, pady=6, sticky='w')
        ctk.CTkLabel(cfg, text='平台URL').grid(row=1, column=2, padx=6, pady=6, sticky='w')
        ctk.CTkEntry(cfg, textvariable=self.platform_url_var, width=260).grid(row=1, column=3, padx=6, pady=6, sticky='w')
        ctk.CTkLabel(cfg, text='并发数').grid(row=2, column=0, padx=6, pady=6, sticky='w')
        self.concurrent_sel_var = tkinter.StringVar(value=str(self.concurrent_var.get()))
        self.concurrent_sel = ctk.CTkComboBox(cfg, values=[str(i) for i in range(1,21)], variable=self.concurrent_sel_var, command=lambda v: self.concurrent_var.set(int(v)), width=120)
        self.concurrent_sel.grid(row=2, column=1, padx=6, pady=6, sticky='w')
        ctk.CTkLabel(cfg, text='元素超时(ms)').grid(row=2, column=2, padx=6, pady=6, sticky='w')
        ctk.CTkEntry(cfg, textvariable=self.timeout_ms_var, width=120).grid(row=2, column=3, padx=6, pady=6, sticky='w')
        ctk.CTkLabel(cfg, text='检测间隔(ms)').grid(row=3, column=0, padx=6, pady=6, sticky='w')
        self.poll_sel_var = tkinter.StringVar(value=str(self.poll_ms_var.get()))
        self.poll_sel = ctk.CTkComboBox(cfg, values=['300','500','700'], variable=self.poll_sel_var, command=lambda v: self.poll_ms_var.set(int(v)), width=120)
        self.poll_sel.grid(row=3, column=1, padx=6, pady=6, sticky='w')
        ctk.CTkCheckBox(cfg, text='启用自动化注册 (XPath)', variable=self.enable_xpath_var).grid(row=3, column=2, padx=6, pady=6, sticky='w')
        ctk.CTkLabel(cfg, text='XPath文件').grid(row=4, column=0, padx=6, pady=6, sticky='w')
        ctk.CTkEntry(cfg, textvariable=self.xpath_var, width=620).grid(row=4, column=1, padx=6, pady=6, sticky='w', columnspan=2)
        ctk.CTkButton(cfg, text='浏览', command=self.choose_xpath, width=80).grid(row=4, column=3, padx=6, pady=6, sticky='w')

        ctk.CTkLabel(self, text='运行日志').pack(anchor='w', padx=12)
        self.log = ctk.CTkTextbox(self, height=220, width=920)
        self.log.pack(fill='both', expand=True, padx=12, pady=6)

        btns = ctk.CTkFrame(self)
        btns.pack(fill='x', padx=12, pady=6)
        ctk.CTkButton(btns, text='开始注册', command=self.start_registration).pack(side='left', padx=6)
        ctk.CTkButton(btns, text='停止注册', command=self.stop_registration).pack(side='left', padx=6)
        ctk.CTkButton(btns, text='测试连接', command=self.test_connection).pack(side='left', padx=6)
        ctk.CTkButton(btns, text='保存参数', command=self.save_config).pack(side='left', padx=6)
        ctk.CTkButton(btns, text='恢复默认', command=self.reset_defaults).pack(side='left', padx=6)
        ctk.CTkButton(btns, text='退出', command=self.destroy, fg_color='red').pack(side='right', padx=6)

    def choose_csv(self):
        p = filedialog.askopenfilename(filetypes=[('CSV','*.csv'), ('All','*')])
        if p:
            self.csv_var.set(p)

    def choose_xpath(self):
        p = filedialog.askopenfilename(filetypes=[('JSON','*.json'), ('All','*')])
        if p:
            self.xpath_var.set(p)

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

    def progress_cb(self, data):
        self.cnt_total_var.set(str(data.get('total',0)))
        self.cnt_success_var.set(str(data.get('success',0)))
        self.cnt_fail_var.set(str(data.get('fail',0)))

    def start_registration(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.enable_xpath_var.get():
            self.append_log('未启用自动化注册')
            return
        self.stop_event.clear()
        args = (
            self.csv_var.get(),
            self.xpath_var.get(),
            self.platform_url_var.get(),
            self.bit_url_var.get(),
            self.bit_secret_var.get() or None,
            int(self.concurrent_var.get()),
            int(self.timeout_ms_var.get()),
            int(self.poll_ms_var.get()),
        )
        def run():
            try:
                run_batch(*args, logger=self.append_log, stop_event=self.stop_event, progress_cb=self.progress_cb)
                self.append_log('任务完成')
            except Exception as e:
                self.append_log(str(e))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def stop_registration(self):
        self.stop_event.set()
        self.append_log('已请求停止')

    def test_connection(self):
        import requests
        try:
            base = self.bit_url_var.get().rstrip('/')
            headers = {'Content-Type':'application/json'}
            if self.bit_secret_var.get():
                headers['Authorization'] = self.bit_secret_var.get()
            payload = {'name': 'health-check', 'proxyMethod': 2, 'proxyType': 'noproxy', 'browserFingerPrint': {'coreVersion': '124'}}
            def try_open(url_base: str) -> bool:
                try:
                    r = requests.post(f"{url_base}/browser/update", headers=headers, data=json.dumps(payload), timeout=5)
                    r.raise_for_status()
                    data = r.json()
                    d = data.get('data')
                    bid = None
                    if isinstance(d, dict):
                        bid = d.get('id')
                    elif isinstance(d, str):
                        bid = d
                    if not bid:
                        bid = data.get('id')
                    if not bid:
                        self.append_log('连接失败: 未返回id')
                        return False
                    r2 = requests.post(f"{url_base}/browser/open", headers=headers, data=json.dumps({'id':bid}), timeout=10)
                    r2.raise_for_status()
                    data2 = r2.json().get('data',{})
                    self.append_log(f"连接成功: {data2}")
                    try:
                        requests.post(f"{url_base}/browser/close", headers=headers, data=json.dumps({'id':bid}), timeout=5)
                    except Exception:
                        pass
                    return True
                except Exception as e:
                    self.append_log(f"连接尝试失败: {url_base} -> {e}")
                    return False
            if try_open(base):
                return
            ports = [54345, 54346, 54321, 54322, 50325, 55555]
            for p in ports:
                alt = f"http://127.0.0.1:{p}"
                if alt == base:
                    continue
                if try_open(alt):
                    self.bit_url_var.set(alt)
                    self.append_log(f"已自动切换比特浏览器接口到: {alt}")
                    return
            self.append_log("连接失败: 未发现可用的比特浏览器接口，请确认比特浏览器已启动并开启本地API")
        except Exception as e:
            self.append_log(f"连接失败: {e}")

    def save_config(self):
        cfg = {
            'csv': self.csv_var.get(),
            'xpaths': self.xpath_var.get(),
            'bit_url': self.bit_url_var.get(),
            'bit_secret': self.bit_secret_var.get(),
            'platform_url': self.platform_url_var.get(),
            'concurrency': int(self.concurrent_var.get()),
            'timeout_ms': int(self.timeout_ms_var.get()),
            'poll_ms': int(self.poll_ms_var.get()),
        }
        try:
            with open('gui_config.json', 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self.append_log('参数已保存')
        except Exception as e:
            self.append_log(str(e))

    def reset_defaults(self):
        self.csv_var.set('/Users/jerry/Documents/GitHub/KL_AI/kl-mail.csv')
        self.xpath_var.set('/Users/jerry/Documents/GitHub/KL_AI/kling_xpaths.json')
        self.bit_url_var.set('http://127.0.0.1:54345')
        self.bit_secret_var.set(os.environ.get('BITBROWSER_SECRET') or '')
        self.platform_url_var.set('https://klingai.com/global')
        self.concurrent_var.set(1)
        self.timeout_ms_var.set(100000)
        self.poll_ms_var.set(500)

    def _load_config(self):
        try:
            with open('gui_config.json', 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            self.csv_var.set(cfg.get('csv', self.csv_var.get()))
            self.xpath_var.set(cfg.get('xpaths', self.xpath_var.get()))
            self.bit_url_var.set(cfg.get('bit_url', self.bit_url_var.get()))
            self.bit_secret_var.set(cfg.get('bit_secret', self.bit_secret_var.get()))
            self.platform_url_var.set(cfg.get('platform_url', self.platform_url_var.get()))
            self.concurrent_var.set(int(cfg.get('concurrency', self.concurrent_var.get())))
            self.timeout_ms_var.set(int(cfg.get('timeout_ms', self.timeout_ms_var.get())))
            self.poll_ms_var.set(int(cfg.get('poll_ms', self.poll_ms_var.get())))
            self.concurrent_sel_var.set(str(self.concurrent_var.get()))
            self.poll_sel_var.set(str(self.poll_ms_var.get()))
            self.append_log('已加载上次保存的参数')
            try:
                self.load_csv_preview()
            except Exception:
                pass
        except Exception:
            pass


if __name__ == '__main__':
    App().mainloop()
