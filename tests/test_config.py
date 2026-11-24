#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ytcc_config 单元测试 + GUI 集成测试。

运行：python -m unittest discover -s tests -v
所有用例都用 YTCC_CONFIG_PATH 指向临时目录，绝不碰真实的 %APPDATA% 配置。
"""
import os
import sys
import time
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ytcc_config


class ConfigTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ytcc_cfg_test_")
        self.cfg_path = os.path.join(self.tmp, "config.ini")
        self._saved_env = os.environ.get("YTCC_CONFIG_PATH")
        os.environ["YTCC_CONFIG_PATH"] = self.cfg_path

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("YTCC_CONFIG_PATH", None)
        else:
            os.environ["YTCC_CONFIG_PATH"] = self._saved_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    def test_env_override_respected(self):
        self.assertEqual(ytcc_config.config_path(), os.path.abspath(self.cfg_path))

    def test_missing(self):
        cfg, status = ytcc_config.load_config()
        self.assertEqual(status, "missing")
        self.assertEqual(cfg["cc_host"], "")

    def test_save_load_roundtrip(self):
        ytcc_config.save_config("http://127.0.0.1:10888", "uuid-abc",
                                "p@ss:w0rd%含中文", "http://127.0.0.1:10808",
                                proxy_mode="all")
        cfg, status = ytcc_config.load_config()
        self.assertEqual(status, "ok")
        self.assertEqual(cfg["cc_host"], "http://127.0.0.1:10888")
        self.assertEqual(cfg["cc_uuid"], "uuid-abc")
        self.assertEqual(cfg["cc_password"], "p@ss:w0rd%含中文")
        self.assertEqual(cfg["proxy"], "http://127.0.0.1:10808")
        self.assertEqual(cfg["proxy_mode"], "all")
        self.assertGreater(cfg["saved_at"], 0)

    def test_legacy_config_defaults_to_system(self):
        """旧版配置没有 proxy_mode 字段：读回时按 system（采用系统代理），不能崩/不能空。"""
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            fh.write("[ytcc-dl]\ncc_host = h\ncc_uuid = u\ncc_password = p\n"
                     "proxy = http://127.0.0.1:10808\n"
                     f"saved_at = {int(time.time())}\n")
        cfg, status = ytcc_config.load_config()
        self.assertEqual(status, "ok")
        self.assertEqual(cfg["proxy_mode"], "system")

    def test_password_percent_not_interpolated(self):
        """密码含 % 时若用默认 BasicInterpolation 会炸——必须 interpolation=None。"""
        weird = "100%secret%=x%(y)s"
        ytcc_config.save_config("h", "u", weird, "")
        cfg, status = ytcc_config.load_config()
        self.assertEqual(status, "ok")
        self.assertEqual(cfg["cc_password"], weird)

    def test_password_encrypted_in_file(self):
        """落盘的密码绝不能是明文（Windows：dpapi: 密文；其他平台：明文退化+警告头）。"""
        secret = "SuperSecret123"
        ytcc_config.save_config("h", "u", secret, "")
        with open(self.cfg_path, encoding="utf-8") as fh:
            raw = fh.read()
        if ytcc_config._dpapi_available():
            self.assertIn("dpapi:", raw)
            self.assertNotIn(secret, raw)
        else:
            self.assertIn("明文存储", raw)

    def test_expired_self_destruct(self):
        """超期配置：load 返回 expired 且文件被删。"""
        ytcc_config.save_config("h", "u", "p", "")
        # 把 saved_at 改到 31 天前（直接改文件，模拟「放了很久没用」）
        old = str(int(time.time()) - 31 * 86400)
        with open(self.cfg_path, encoding="utf-8") as fh:
            raw = fh.read()
        import re as _re
        raw = _re.sub(r"saved_at\s*=\s*\d+", "saved_at = " + old, raw)
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            fh.write(raw)
        cfg, status = ytcc_config.load_config()
        self.assertEqual(status, "expired")
        self.assertEqual(cfg["cc_host"], "")
        self.assertFalse(os.path.exists(self.cfg_path))

    def test_zero_saved_at_treated_as_expired(self):
        ytcc_config.save_config("h", "u", "p", "")
        with open(self.cfg_path, encoding="utf-8") as fh:
            raw = fh.read()
        import re as _re
        raw = _re.sub(r"saved_at\s*=\s*\d+", "saved_at = 0", raw)
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            fh.write(raw)
        _cfg, status = ytcc_config.load_config()
        self.assertEqual(status, "expired")

    def test_corrupt_file(self):
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            fh.write("这不是合法 INI ===!!!\n\x00\xff")
        cfg, status = ytcc_config.load_config()
        self.assertEqual(status, "error")
        self.assertEqual(cfg["cc_host"], "")

    def test_wrong_section(self):
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            fh.write("[other]\ncc_host = h\n")
        _cfg, status = ytcc_config.load_config()
        self.assertEqual(status, "error")

    def test_undecryptable_password_warns_and_blanks(self):
        """密文损坏（换机器/换用户）：告警 + 密码置空，绝不把乱码当密码用。"""
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            fh.write("[ytcc-dl]\ncc_host = h\ncc_uuid = u\n"
                     "cc_password = dpapi:AAAAcorruptedBBBB\n"
                     f"saved_at = {int(time.time())}\n")
        cfg, status = ytcc_config.load_config()
        self.assertEqual(status, "ok")
        self.assertEqual(cfg["cc_password"], "")
        self.assertIn("warning", cfg)

    def test_plaintext_fallback_decrypt(self):
        """无 dpapi: 前缀的值按明文返回（非 Windows 平台保存的配置）。"""
        self.assertEqual(ytcc_config._decrypt_secret("plain-text", []), "plain-text")
        self.assertEqual(ytcc_config._decrypt_secret("", []), "")

    def test_delete_config(self):
        ytcc_config.save_config("h", "u", "p", "")
        self.assertTrue(ytcc_config.delete_config())
        self.assertFalse(ytcc_config.delete_config())
        _cfg, status = ytcc_config.load_config()
        self.assertEqual(status, "missing")


class GuiConfigIntegrationTest(unittest.TestCase):
    """GUI 集成：启动预填 / 保存按钮 / 平台切换不冲掉自定义代理。
    无显示环境（headless CI）自动跳过。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ytcc_gui_test_")
        self.cfg_path = os.path.join(self.tmp, "config.ini")
        self._saved_env = os.environ.get("YTCC_CONFIG_PATH")
        os.environ["YTCC_CONFIG_PATH"] = self.cfg_path

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("YTCC_CONFIG_PATH", None)
        else:
            os.environ["YTCC_CONFIG_PATH"] = self._saved_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _new_app(self):
        import ytcc_dl
        try:
            ytcc_dl._ensure_tk()
            root = ytcc_dl.tk.Tk()
        except Exception:
            self.skipTest("无 tkinter / 显示环境，跳过 GUI 测试")
        app = ytcc_dl.App(root)
        return root, app

    def test_prefill_and_save_roundtrip(self):
        root1, app1 = self._new_app()
        try:
            # 无配置启动：占位符在，日志提示尚未加载
            app1.e_host.delete(0, "end")
            app1.e_host.insert(0, "http://127.0.0.1:10888")
            app1.e_uuid.delete(0, "end")
            app1.e_uuid.insert(0, "test-uuid")
            app1.e_pass.delete(0, "end")
            app1.e_pass.insert(0, "test-pass")
            app1.e_proxy.delete(0, "end")
            app1.e_proxy.insert(0, "http://127.0.0.1:9999")
            app1.on_save_config()  # 成功路径无弹窗
            self.assertTrue(os.path.exists(self.cfg_path))
        finally:
            root1.destroy()
        # 第二次启动：四个字段全部预填
        root2, app2 = self._new_app()
        try:
            self.assertEqual(app2.e_host.get(), "http://127.0.0.1:10888")
            self.assertEqual(app2.e_uuid.get(), "test-uuid")
            self.assertEqual(app2.e_pass.get(), "test-pass")
            self.assertEqual(app2.e_proxy.get(), "http://127.0.0.1:9999")
            self.assertEqual(app2.var_proxy_mode.get(), "采用系统代理（推荐）")
        finally:
            root2.destroy()

    def test_platform_switch_keeps_proxy_and_updates_route(self):
        """代理地址不随平台切换被冲掉；平台提示反映真实路由；海外三站排前面。"""
        import ytcc_dl
        root, app = self._new_app()
        try:
            app.e_proxy.delete(0, "end")
            app.e_proxy.insert(0, "http://127.0.0.1:9999")
            for plat in ("YouTube", "X (Twitter)", "TikTok"):
                app.var_platform.set(plat)
                app.on_platform_change()
                self.assertEqual(app.e_proxy.get(), "http://127.0.0.1:9999")
                self.assertIn("🌐 走代理", app.lbl_note.cget("text"))
            for plat in ("Bilibili (B站)", "抖音 (Douyin)", "微博 (Weibo)",
                         "小红书 (Xiaohongshu)"):
                app.var_platform.set(plat)
                app.on_platform_change()
                self.assertEqual(app.e_proxy.get(), "http://127.0.0.1:9999")
                self.assertIn("🏠 直连", app.lbl_note.cget("text"))
            self.assertEqual(list(ytcc_dl.OVERSEAS_PLATFORMS),
                             list(ytcc_dl.PLATFORMS)[:3])
        finally:
            root.destroy()

    def test_proxy_mode_dropdown_roundtrip(self):
        import ytcc_dl
        root, app = self._new_app()
        try:
            app.e_proxy.delete(0, "end")
            app.e_proxy.insert(0, "http://127.0.0.1:9999")
            app.var_platform.set("抖音 (Douyin)")
            app.var_proxy_mode.set(ytcc_dl.PROXY_MODE_LABELS["all"])
            app.on_platform_change()
            self.assertIn("🌐 走代理", app.lbl_note.cget("text"))  # all：国内也代理
            app.var_proxy_mode.set(ytcc_dl.PROXY_MODE_LABELS["none"])
            app.on_platform_change()
            self.assertIn("🏠 直连", app.lbl_note.cget("text"))    # none：全直连
            app.var_proxy_mode.set(ytcc_dl.PROXY_MODE_LABELS["auto"])
            app.e_host.delete(0, "end")
            app.e_host.insert(0, "http://127.0.0.1:10888")
            app.e_uuid.delete(0, "end")
            app.e_uuid.insert(0, "u1")
            app.e_pass.delete(0, "end")
            app.e_pass.insert(0, "p1")
            app.on_save_config()
            cfg, status = ytcc_config.load_config()
            self.assertEqual(status, "ok")
            self.assertEqual(cfg["proxy_mode"], "auto")
        finally:
            root.destroy()

    def test_autosave_after_fetch_success(self):
        """_autosave_config 把凭据写盘（拉 cookie 成功的回调路径）。"""
        root, app = self._new_app()
        try:
            app._autosave_config("http://127.0.0.1:10888", "u1", "p1")
            cfg, status = ytcc_config.load_config()
            self.assertEqual(status, "ok")
            self.assertEqual(cfg["cc_uuid"], "u1")
        finally:
            root.destroy()


class ProxyModeTest(unittest.TestCase):
    """代理分流纯函数：不依赖 GUI / 网络。"""

    def setUp(self):
        import ytcc_dl
        self.ytcc = ytcc_dl

    def test_auto_only_overseas(self):
        p = "http://127.0.0.1:10808"
        for plat in self.ytcc.OVERSEAS_PLATFORMS:
            self.assertEqual(self.ytcc.effective_proxy(plat, "auto", p), p)
        for plat in ("Bilibili (B站)", "抖音 (Douyin)", "微博 (Weibo)",
                     "小红书 (Xiaohongshu)"):
            self.assertEqual(self.ytcc.effective_proxy(plat, "auto", p), "")

    def test_all_and_none(self):
        y = self.ytcc
        p = "socks5://127.0.0.1:1080"
        for plat in y.PLATFORMS:
            self.assertEqual(y.effective_proxy(plat, "all", p), p)
            self.assertEqual(y.effective_proxy(plat, "none", p), "")
        # 全部走代理但没填地址 → 仍直连，不能把空串当有效代理传给 yt-dlp
        self.assertEqual(y.effective_proxy("YouTube", "all", ""), "")

    def test_system_mode_and_one_click_toggle(self):
        y = self.ytcc
        p = "http://127.0.0.1:10808"
        # system 模式：手填地址优先；海外走代理、国内直连（与 auto 同分流）
        self.assertEqual(y.effective_proxy("YouTube", "system", p), p)
        self.assertEqual(y.effective_proxy("抖音 (Douyin)", "system", p), "")
        # 一键代理关闭：无论什么模式都强制直连
        self.assertEqual(y.effective_proxy("YouTube", "system", p, enabled=False), "")
        self.assertEqual(y.effective_proxy("YouTube", "all", p, enabled=False), "")
        # system 且地址留空时回落系统代理（本机有系统代理则应拿到非空值）
        import unittest.mock as _mock
        with _mock.patch.object(y, "detect_system_proxy", return_value=(p, "test")):
            self.assertEqual(y.effective_proxy("YouTube", "system", ""), p)
            self.assertEqual(y.effective_proxy("抖音 (Douyin)", "system", ""), "")

    def test_mode_key_normalization(self):
        y = self.ytcc
        self.assertEqual(y.proxy_mode_key(y.PROXY_MODE_LABELS["all"]), "all")
        self.assertEqual(y.proxy_mode_key("智能分流（仅海外3站）"), "auto")
        self.assertEqual(y.proxy_mode_key("采用系统代理（推荐）"), "system")
        self.assertEqual(y.proxy_mode_key("weird-value"), "system")  # 非法值回落默认 system
        self.assertEqual(y.proxy_mode_key(True), "system")  # CLI 无值 flag

    def test_version_and_file_naming_material(self):
        import version
        self.assertTrue(version.VERSION)
        self.assertEqual(len(version.VERSION.split(".")), 3)


if __name__ == "__main__":
    unittest.main()
