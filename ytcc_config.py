#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ytcc-dl 本地配置持久化——「一次录入，本机复用」。

解决的问题：CookieCloud host / UUID / 密码、代理地址这些本机固定参数，
每次打开 GUI 重填一遍很烦。本模块把它们存进一个本地 INI 文件，
下次启动自动预填；命令行与 server 模式也可回退读取（显式参数优先）。

安全设计（威胁模型 = 同机他人翻看 / 配置文件被顺手拷走）：
  1. 存放位置：%APPDATA%\\ytcc-dl\\config.ini（Windows）或
     ~/.config/ytcc-dl/config.ini（其他平台）。刻意不放 exe 同目录——
     exe 可能位于只读位置，也可能被整体拷贝分发，凭据不该跟着二进制走。
     环境变量 YTCC_CONFIG_PATH 可覆盖路径（测试 / 高级用户）。
  2. 密码字段：Windows 用 DPAPI（CryptProtectData）加密后以 base64 存储，
     密文只能被「同一个 Windows 用户、同一台机器」解密——拷到别的
     机器 / 别的用户下就是一堆废字节。零第三方依赖（纯 ctypes 调 crypt32）。
     非 Windows 平台没有等价物，退化为明文存储（文件头有警告注释）。
     host / uuid / 代理保持明文：它们本来就可能需要手改，单独泄露危害
     有限；真正的 secret 只有密码，整文件加密只会让配置变成不可编辑的黑盒。
  3. 自动销毁：文件带 saved_at 时间戳，超过 CONFIG_MAX_AGE_DAYS 天未
     「成功使用」（拉取 cookie 成功或手动保存都会续期）再次打开时自动删除，
     防止陈旧的登录凭据无限期躺在磁盘上。
  4. 原子写入：先写 .tmp 再 os.replace，崩溃不会留下半截配置；
     POSIX 下 chmod 600。

用法：
    import ytcc_config
    cfg, status = ytcc_config.load_config()   # status: ok/missing/expired/error
    path = ytcc_config.save_config(host, uuid, password, proxy, proxy_mode="system")
"""

import base64
import configparser
import ctypes
import os
import time

# 配置自毁期限：超过这么多天没有「成功使用」记录，下次打开自动删除配置文件
CONFIG_MAX_AGE_DAYS = 30
SECTION = "ytcc-dl"
_ENC_PREFIX = "dpapi:"          # 密码字段加密标记（无此前缀按明文处理）
_CRYPTPROTECT_UI_FORBIDDEN = 0x1  # 禁止 DPAPI 弹任何交互窗口（无头环境必需）


def config_path():
    """配置文件路径：YTCC_CONFIG_PATH > Windows APPDATA > XDG/home 回退。"""
    env = os.environ.get("YTCC_CONFIG_PATH")
    if env:
        return os.path.abspath(env)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config")
    return os.path.join(base, "ytcc-dl", "config.ini")


# ---------------------------------------------------------------------------
# Windows DPAPI（CryptProtectData / CryptUnprotectData）——ctypes 直调，零依赖
# ---------------------------------------------------------------------------
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_available():
    return os.name == "nt" and hasattr(ctypes, "windll")


def _dpapi(encrypt, data):
    """DPAPI 加解密。encrypt=True 保护数据，False 还原数据。
    失败抛异常，由调用方决定降级路径（提示重填，绝不静默用错值）。"""
    if os.name != "nt":
        raise OSError("DPAPI 仅在 Windows 可用")
    # create_string_buffer 会比 data 多带一个 NUL，无妨——cbData 按真实长度传
    in_buf = ctypes.create_string_buffer(data)
    blob_in = _DATA_BLOB(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    p_blob = ctypes.POINTER(_DATA_BLOB)
    crypt32 = ctypes.windll.crypt32
    if encrypt:
        fn = crypt32.CryptProtectData
        # (pDataIn, szDataDescr, pOptionalEntropy, pvReserved, pPromptStruct, dwFlags, pDataOut)
        fn.argtypes = [p_blob, ctypes.c_wchar_p, p_blob, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_ulong, p_blob]
        descr = "ytcc-dl credential"
    else:
        fn = crypt32.CryptUnprotectData
        # 第二参数是 LPWSTR*（描述符出参），传 NULL 表示不需要
        fn.argtypes = [p_blob, ctypes.POINTER(ctypes.c_wchar_p), p_blob,
                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, p_blob]
        descr = None
    fn.restype = ctypes.c_int
    ok = fn(ctypes.byref(blob_in), descr, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32 = ctypes.windll.kernel32
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def _encrypt_secret(plain):
    """密码入库：Windows 走 DPAPI；其他平台明文退化（文件头有警告）。"""
    if not plain:
        return ""
    if _dpapi_available():
        blob = _dpapi(True, plain.encode("utf-8"))
        return _ENC_PREFIX + base64.b64encode(blob).decode("ascii")
    return plain


def _decrypt_secret(stored, warnings):
    """密码出库：dpapi: 前缀走 DPAPI 解密；失败不静默——告警 + 返回空串让用户重填。"""
    if not stored:
        return ""
    if stored.startswith(_ENC_PREFIX):
        try:
            raw = base64.b64decode(stored[len(_ENC_PREFIX):])
            return _dpapi(False, raw).decode("utf-8")
        except Exception:
            warnings.append(
                "保存的密码无法解密（可能更换了 Windows 用户或机器），请重新输入密码")
            return ""
    return stored  # 明文回退（非 Windows 平台保存的配置）


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------
def _empty_cfg():
    return {"cc_host": "", "cc_uuid": "", "cc_password": "", "proxy": "",
            "proxy_mode": "system", "saved_at": 0}


def load_config():
    """读取本地配置，返回 (cfg, status)。

    status: ok=已加载 / missing=无配置文件 / expired=超期已自动销毁 / error=文件损坏已忽略。
    cfg 恒为 dict（六个键齐全，值可能为空串）；proxy_mode 缺省按 "system"，
    兼容旧版配置文件。密码解密失败时 cc_password 置空并在 cfg["warning"]
    附原因（绝不把乱码当密码用）。
    """
    path = config_path()
    if not os.path.isfile(path):
        return _empty_cfg(), "missing"
    # interpolation=None 是关键：密码里含 % 会被 BasicInterpolation 当占位符炸掉
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(path, encoding="utf-8")
        if not cp.has_section(SECTION):
            return _empty_cfg(), "error"
        sec = cp[SECTION]
        saved_at = int(sec.get("saved_at", "0") or 0)
    except Exception:
        return _empty_cfg(), "error"
    if saved_at <= 0 or (time.time() - saved_at) > CONFIG_MAX_AGE_DAYS * 86400:
        # 超期自毁：删文件，按「无配置」处理（调用方提示用户重填）
        try:
            os.remove(path)
        except OSError:
            pass
        return _empty_cfg(), "expired"
    warnings = []
    cfg = {
        "cc_host": (sec.get("cc_host") or "").strip(),
        "cc_uuid": (sec.get("cc_uuid") or "").strip(),
        "cc_password": _decrypt_secret(sec.get("cc_password") or "", warnings),
        "proxy": (sec.get("proxy") or "").strip(),
        "proxy_mode": (sec.get("proxy_mode") or "system").strip() or "system",
        "saved_at": saved_at,
    }
    if warnings:
        cfg["warning"] = "；".join(warnings)
    return cfg, "ok"


def save_config(cc_host, cc_uuid, cc_password, proxy, proxy_mode="system"):
    """保存配置（原子写入），返回文件路径。密码字段按平台能力加密。

    proxy_mode 取 system / auto / all / none；旧调用方不传时按 system（采用系统代理）兼容。
    """
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # interpolation=None：密码含 % 时默认插值语法会炸
    cp = configparser.ConfigParser(interpolation=None)
    cp[SECTION] = {
        "cc_host": (cc_host or "").strip(),
        "cc_uuid": (cc_uuid or "").strip(),
        "cc_password": _encrypt_secret(cc_password or ""),
        "proxy": (proxy or "").strip(),
        "proxy_mode": (proxy_mode or "system").strip() or "system",
        "saved_at": str(int(time.time())),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        if not _dpapi_available():
            fh.write("# ⚠ 非 Windows 平台：密码为明文存储（无 DPAPI 等价物），请自行注意文件安全\n")
        fh.write("# ytcc-dl 本地配置：首次填写后保存，之后自动预填；"
                 f"{CONFIG_MAX_AGE_DAYS} 天不使用自动销毁；删除本文件=重置\n")
        cp.write(fh)
    os.replace(tmp, path)  # 原子替换，避免崩溃留下半截文件
    try:
        if os.name == "posix":
            os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def delete_config():
    """删除配置文件（用户主动重置用）。返回是否删掉了东西。"""
    try:
        os.remove(config_path())
        return True
    except OSError:
        return False
