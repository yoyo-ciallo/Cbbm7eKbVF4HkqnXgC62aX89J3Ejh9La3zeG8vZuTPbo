import json
import os
import re
import sys
import time
import uuid
import math
import random
import string
import secrets
import hashlib
import base64
import threading
import argparse
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, quote
from dataclasses import dataclass
from typing import Any, Dict, Optional, List
import urllib.parse
import urllib.request
import urllib.error

from curl_cffi import requests

# Outlook Reader for OTP verification (已移除，改用 curl 調用)
# from outlook_reader import OutlookReader, wait_for_otp_code, check_and_save_cookies

# ==========================================
# Custom Domain Email Generator
# 使用自己的域名 + Cloudflare Email Routing
# ==========================================

# 可用域名列表
EMAIL_DOMAINS = ["y0918.qzz.io"]
EMAIL_PREFIX = "yoyo"

# 域名轮询索引
_domain_index = 0

def get_next_domain() -> str:
    """轮询获取下一个域名"""
    global _domain_index
    domain = EMAIL_DOMAINS[_domain_index % len(EMAIL_DOMAINS)]
    _domain_index += 1
    return domain

# ==========================================
# OTP 獲取配置
# 使用自定義 Cloudflare Worker 郵箱服務
# ==========================================
WORKER_DOMAIN = "https://curl.y0918.dpdns.org"
WORKER_API_KEY = "PzbYFjs32s6b1WKJp0FYNwqL3ftKp5tsFD"
OTP_WAIT_SECONDS = 75  # 發送 OTP 後等待秒數


def generate_email() -> str:
    """生成隨機郵箱地址 yoyo-{random}@domain.com"""
    random_part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    domain = get_next_domain()  # 轮询获取域名
    return f"{EMAIL_PREFIX}-{random_part}@{domain}"


def generate_password() -> str:
    """生成隨機密碼"""
    # 密碼要求：至少8位，包含大小寫和數字
    return secrets.token_urlsafe(16)[:16] + "A1"


# ==========================================
# Worker 郵箱管理
# 使用自定義 Cloudflare Worker 郵箱服務
# ==========================================

def create_worker_mailbox(proxies: Any = None) -> tuple:
    """通過 Worker API 創建新郵箱，返回 (email, mailbox_id)"""
    import subprocess
    
    url = f"{WORKER_DOMAIN}/api/remail?key={WORKER_API_KEY}"
    
    try:
        # Worker API 不需要代理
        cmd = ["curl", "-s", "-m", "10", "-X", "POST", url]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15
        )
        
        import json
        data = json.loads(result.stdout.strip())
        
        email = data.get("email", "")
        mailbox_id = data.get("id", "")
        
        if email and mailbox_id:
            print(f"[*] Worker 郵箱創建成功: {email}")
            return email, mailbox_id
        
        print(f"[!] Worker API 響應異常: {result.stdout[:200]}")
        return "", ""
        
    except Exception as e:
        print(f"[!] 創建郵箱失敗: {e}")
        return "", ""


def get_otp_from_worker_mailbox(mailbox_id: str, proxies: Any = None) -> str:
    """通過 Worker API 獲取 OTP"""
    import subprocess
    import json
    
    max_attempts = 15  # 15次
    attempt = 0
    
    while attempt < max_attempts:
        attempt += 1
        
        # 獲取收件箱（Worker API 不需要代理）
        inbox_url = f"{WORKER_DOMAIN}/api/inbox?key={WORKER_API_KEY}&mailbox_id={mailbox_id}"
        
        cmd = ["curl", "-s", "-m", "5", inbox_url]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if not result.stdout.strip():
            print(f"[!] Worker API 無響應: {result.stderr}")
            time.sleep(5)
            continue
        
        try:
            inbox_data = json.loads(result.stdout.strip())
        except json.JSONDecodeError as e:
            print(f"[!] JSON 解析失敗: {e}")
            time.sleep(5)
            continue
        
        # Worker API 返回 list 直接是郵件陣列，不是 {"mails": [...]} 物件
        mails = inbox_data if isinstance(inbox_data, list) else inbox_data.get("mails", [])
        print(f"[*] 檢查 {attempt}/{max_attempts}, 郵件數: {len(mails)}")
        
        code = None
        
        if mails:
            # 獲取最新郵件
            latest_mail = mails[0]
            mail_id = latest_mail.get("mail_id", "")
            
            if mail_id:
                # 獲取郵件內容
                mail_url = f"{WORKER_DOMAIN}/api/mail?key={WORKER_API_KEY}&id={mail_id}"
                
                cmd = ["curl", "-s", "-m", "5", mail_url]
                
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                mail_data = json.loads(result.stdout.strip())
                content = mail_data.get("content", "")
                
                # 解析驗證碼
                
                # 模式1: font-size:24px 樣式中的6位數字
                match = re.search(r'font-size:24px[^>]*>[\s\n]*(\d{6})[\s\n]*<', content)
                if match:
                    code = match.group(1)
                
                # 模式2: Menlo/Monaco 字體樣式
                if not code:
                    match = re.search(r'(?:Menlo|Monaco|Lucida Console)[^>]*>[\s\n]*(\d{6})[\s\n]*<', content)
                    if match:
                        code = match.group(1)
                
                # 模式3: 通用6位數字
                if not code:
                    match = re.search(r'>\s*(\d{6})\s*<', content)
                    if match:
                        code = match.group(1)
                
                # 模式4: 驗證碼關鍵詞後面的6位數字
                if not code:
                    match = re.search(r'(?:驗證碼|code|verification|otp)[\s\W]*(\d{6})', content, re.I)
                    if match:
                        code = match.group(1)
        
        # 每 5 秒檢查一次
        if code and len(code) == 6 and code.isdigit():
            print(f"[*] 收到 OTP: {code}")
            return code
        
        print(f"[*] 等待中 {attempt}/{max_attempts}...")
        time.sleep(5)
    
    print(" 超時，未收到 OTP")
    return ""


# ==========================================
# OTP Reader via curl (舊版外部 API - 保留相容)
# ==========================================

def get_oai_code_via_curl(email: str, proxies: Any = None) -> str:
    """使用 Worker API 獲取 OpenAI 驗證碼"""
    # 這裡需要傳入 mailbox_id，但舊接口沒有
    # 回退到使用外部 API
    print("[*] 使用 Worker API 獲取 OTP...")
    
    # 嘗試從環境或全局變量獲取 mailbox_id
    global _current_mailbox_id
    mailbox_id = getattr(sys.modules[__name__], '_current_mailbox_id', None)
    
    if mailbox_id:
        return get_otp_from_worker_mailbox(mailbox_id, proxies)
    
    # 如果沒有 mailbox_id，回退到舊版 curl 邏輯
    return _get_oai_code_via_curl_legacy(email, proxies)


# 全局變量存儲當前郵箱 ID
_current_mailbox_id = ""


def _get_oai_code_via_curl_legacy(email: str, proxies: Any = None) -> str:
    """舊版外部 API 獲取 OTP（保留相容）"""
    import subprocess
    import re
    
    print(f"[*] 使用外部 API 獲取 OTP: {email}")
    
    # 構建 API URL（固定路徑）
    api_url = f"{WORKER_DOMAIN}/api/remail?key={WORKER_API_KEY}"
    
    max_attempts = 60
    attempt = 0
    
    while attempt < max_attempts:
        attempt += 1
        
        try:
            cmd = ["curl", "-s", "-m", "5", api_url]
            
            if proxies:
                proxy = proxies.get("http", "")
                if proxy:
                    cmd.extend(["-x", proxy])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            html_content = result.stdout.strip()
            
            if not html_content:
                continue
            
            code = None
            
            match = re.search(r'font-size:24px[^>]*>[\s\n]*(\d{6})[\s\n]*<', html_content)
            if match:
                code = match.group(1)
            
            if not code:
                match = re.search(r'(?:Menlo|Monaco|Lucida Console)[^>]*>[\s\n]*(\d{6})[\s\n]*<', html_content)
                if match:
                    code = match.group(1)
            
            if not code:
                match = re.search(r'>\s*(\d{6})\s*<', html_content)
                if match:
                    code = match.group(1)
            
            if not code:
                match = re.search(r'(?:驗證碼|code|verification|otp)[\s\W]*(\d{6})', html_content, re.I)
                if match:
                    code = match.group(1)
            
            if code and len(code) == 6 and code.isdigit():
                print(f"[*] Got OTP: {code}")
                return code
            
        except Exception as e:
            pass
        
        if attempt % 10 == 0:
            print(f"[*] Still waiting for OTP... ({attempt}/{max_attempts})")
        
        time.sleep(1)
    
    print(" Timeout, no OTP received")
    return ""


# 兼容舊函數名稱
def get_oai_code_via_outlook(email: str, proxies: Any = None) -> str:
    """兼容舊接口，實際調用 Worker 版本"""
    return get_oai_code_via_curl(email, proxies)


# ==========================================
# OAuth 授权与辅助函数
# ==========================================

AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_REDIRECT_URI = f"http://localhost:1455/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())


def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _parse_callback_url(callback_url: str) -> Dict[str, Any]:
    candidate = callback_url.strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}

    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate:
            candidate = f"http://{candidate}"
        elif "=" in candidate:
            candidate = f"http://localhost/?{candidate}"

    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)

    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values

    def get1(k: str) -> str:
        v = query.get(k, [""])
        return (v[0] or "").strip()

    code = get1("code")
    state = get1("state")
    error = get1("error")
    error_description = get1("error_description")

    if code and not state and "#" in code:
        code, state = code.split("#", 1)

    if not error and error_description:
        error, error_description = error_description, ""

    return {
        "code": code,
        "state": state,
        "error": error,
        "error_description": error_description,
    }


def _jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        payload = base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii"))
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return {}


def _decode_jwt_segment(seg: str) -> Dict[str, Any]:
    raw = (seg or "").strip()
    if not raw:
        return {}
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode((raw + pad).encode("ascii"))
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _post_form(url: str, data: Dict[str, str], timeout: int = 30) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError(
                    f"token exchange failed: {resp.status}: {raw.decode('utf-8', 'replace')}"
                )
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise RuntimeError(
            f"token exchange failed: {exc.code}: {raw.decode('utf-8', 'replace')}"
        ) from exc


@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str


def generate_oauth_url(
    *, redirect_uri: str = DEFAULT_REDIRECT_URI, scope: str = DEFAULT_SCOPE
) -> OAuthStart:
    state = _random_state()
    code_verifier = _pkce_verifier()
    code_challenge = _sha256_b64url_no_pad(code_verifier)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return OAuthStart(
        auth_url=auth_url,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def submit_callback_url(
    *,
    callback_url: str,
    expected_state: str,
    code_verifier: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    account_email: str = "",
    account_password: str = "",
) -> str:
    cb = _parse_callback_url(callback_url)
    if cb["error"]:
        desc = cb["error_description"]
        raise RuntimeError(f"oauth error: {cb['error']}: {desc}".strip())

    if not cb["code"]:
        raise ValueError("callback url missing ?code=")
    if not cb["state"]:
        raise ValueError("callback url missing ?state=")
    if cb["state"] != expected_state:
        raise ValueError("state mismatch")

    token_resp = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": cb["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )

    access_token = (token_resp.get("access_token") or "").strip()
    refresh_token = (token_resp.get("refresh_token") or "").strip()
    id_token = (token_resp.get("id_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))

    claims = _jwt_claims_no_verify(id_token)
    email = str(claims.get("email") or "").strip()
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()

    now = int(time.time())
    expired_rfc3339 = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0))
    )
    now_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    config = {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": now_rfc3339,
        "email": email,
        "type": "codex",
        "expired": expired_rfc3339,
    }
    
    # Add account credentials if provided
    if account_email:
        config["account_email"] = account_email
    if account_password:
        config["account_password"] = account_password

    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


# ==========================================
# 核心注册逻辑
# ==========================================


def run(proxy: Optional[str]) -> Optional[str]:
    global _current_mailbox_id
    
    proxies: Any = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    s = requests.Session(proxies=proxies, impersonate="chrome")

    try:
        trace = s.get("https://cloudflare.com/cdn-cgi/trace", timeout=10)
        trace = trace.text
        loc_re = re.search(r"^loc=(.+)$", trace, re.MULTILINE)
        loc = loc_re.group(1) if loc_re else None
        print(f"[*] Current IP location: {loc}")
        if loc == "CN" or loc == "HK":
            raise RuntimeError("Proxy check failed - location not supported")
    except Exception as e:
        print(f"[Error] Network check failed: {e}")
        return None

    # ==========================================
    # Step 1: 使用 Worker API 創建郵箱
    # ==========================================
    print("[*] 創建 Worker 郵箱...")
    worker_email, mailbox_id = create_worker_mailbox(proxies)
    
    if worker_email and mailbox_id:
        email = worker_email
        _current_mailbox_id = mailbox_id
        print(f"[*] 使用 Worker 郵箱: {email}")
    else:
        # 回退到自定義域名郵箱
        print("[!] Worker 郵箱創建失敗，回退到自定義域名")
        email = generate_email()
        _current_mailbox_id = ""
        print(f"[*] 使用自定義郵箱: {email}")
    
    password = generate_password()
    print(f"[*] Generated password: [saved for account]")

    oauth = generate_oauth_url()
    url = oauth.auth_url

    try:
        resp = s.get(url, timeout=15)
        did = s.cookies.get("oai-did")
        print(f"[*] Device ID: {did}")

        # Signup body - password will be set later
        signup_body = f'{{"username":{{"value":"{email}","kind":"email"}},"screen_hint":"signup"}}'
        sen_req_body = f'{{"p":"","id":"{did}","flow":"authorize_continue"}}'

        sen_resp = requests.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=sen_req_body,
            proxies=proxies,
            impersonate="chrome",
            timeout=15,
        )

        if sen_resp.status_code != 200:
            print(f"[Error] Sentinel 异常拦截，状态码: {sen_resp.status_code}")
            return None

        sen_token = sen_resp.json()["token"]
        sentinel = f'{{"p": "", "t": "", "c": "{sen_token}", "id": "{did}", "flow": "authorize_continue"}}'

        signup_resp = s.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "referer": "https://auth.openai.com/create-account",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel,
            },
            data=signup_body,
        )
        print(f"[*] Signup form status: {signup_resp.status_code}")
        
        # Check response
        try:
            signup_data = signup_resp.json()
            print(f"[*] Signup response: {signup_data}")
        except:
            print(f"[*] Signup response: {signup_resp.text[:300]}")

        # ==========================================
        # Step 2: Submit password using /user/register
        # ==========================================
        print(f"[*] Submitting password...")
        
        register_body = json.dumps({"password": password, "username": email})
        print(f"[*] Generated password: {password[:4]}****")
        
        pwd_resp = s.post(
            "https://auth.openai.com/api/accounts/user/register",
            headers={
                "referer": "https://auth.openai.com/create-account/password",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel,
            },
            data=register_body,
            proxies=proxies,
        )
        print(f"[*] Password submit status: {pwd_resp.status_code}")
        
        if pwd_resp.status_code != 200:
            print(f"[!] Password response: {pwd_resp.text[:500]}")
            return None
        
        # Get continue_url from register response
        try:
            register_json = pwd_resp.json()
            register_continue = register_json.get("continue_url", "")
            print(f"[*] Register continue_url: {register_continue}")
        except:
            register_continue = ""
            print(f"[*] Register response: {pwd_resp.text[:300]}")

        # ==========================================
        # Step 3: Send OTP
        # ==========================================
        # Use continue_url from register response or default OTP endpoint
        otp_url = register_continue if register_continue else "https://auth.openai.com/api/accounts/email-otp/send"
        print(f"[*] Sending OTP via: {otp_url}")
        
        otp_resp = s.post(
            otp_url,
            headers={
                "referer": "https://auth.openai.com/create-account/password",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel,
            },
        )
        print(f"[*] OTP send status: {otp_resp.status_code}")
        
        # Debug: 打印響應內容
        if otp_resp.status_code != 200:
            print(f"[!] OTP response: {otp_resp.text[:500]}")

        # ==========================================
        # 邊等待邊輪詢 OTP（使用 Worker API）
        # ==========================================
        print(f"[*] 開始輪詢 Worker API 獲取 OTP (最多 75 秒)...")
        
        code = get_otp_from_worker_mailbox(_current_mailbox_id, proxies)
        
        # 如果首次没拿到，进入重试
        if not code:
            print("[!] 首次輪詢超時，開始重試...")
            
            for retry in range(2):  # 最多重試 2 次
                retry_num = retry + 1
                print(f"[*] 重發 OTP (嘗試 {retry_num}/2)...")
                
                otp_resp = s.post(
                    "https://auth.openai.com/api/accounts/passwordless/send-otp",
                    headers={
                        "referer": "https://auth.openai.com/create-account/password",
                        "accept": "application/json",
                        "content-type": "application/json",
                    },
                )
                
                if otp_resp.status_code == 409:
                    print(f"[!] Session 已過期: {otp_resp.text[:200]}")
                    break
                
                if otp_resp.status_code != 200:
                    print(f"[!] OTP resend failed: {otp_resp.text[:200]}")
                    continue
                
                print(f"[*] OTP 已重發，等待新驗證碼...")
                code = get_otp_from_worker_mailbox(_current_mailbox_id, proxies)
                
                if code:
                    print(f"[*] 成功獲取 OTP: {code}")
                    break
        
        if not code:
            print("[!] 未能獲取 OTP")
            return None

        code_body = f'{{"code":"{code}"}}'
        code_resp = s.post(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            headers={
                "referer": "https://auth.openai.com/email-verification",
                "accept": "application/json",
                "content-type": "application/json",
            },
            data=code_body,
        )
        print(f"[*] 验证码校验状态: {code_resp.status_code}")

        create_account_body = '{"name":"Neo","birthdate":"2000-02-20"}'
        create_account_resp = s.post(
            "https://auth.openai.com/api/accounts/create_account",
            headers={
                "referer": "https://auth.openai.com/about-you",
                "accept": "application/json",
                "content-type": "application/json",
            },
            data=create_account_body,
        )
        create_account_status = create_account_resp.status_code
        print(f"[*] 账户创建状态: {create_account_status}")

        if create_account_status != 200:
            print(create_account_resp.text)
            return None

        auth_cookie = s.cookies.get("oai-client-auth-session")
        if not auth_cookie:
            print("[Error] 未能获取到授权 Cookie")
            return None

        # Debug: 打印 cookie 内容
        print(f"[*] Auth Cookie: {auth_cookie[:100]}...")
        
        auth_json = _decode_jwt_segment(auth_cookie.split(".")[0])
        print(f"[*] Auth JSON keys: {list(auth_json.keys())}")
        
        workspaces = auth_json.get("workspaces") or []
        if not workspaces:
            print("[!] 授权 Cookie 里没有 workspace 信息")
            print(f"[*] Available keys: {list(auth_json.keys())}")
            # 嘗試其他可能的 key
            alt_keys = ["workspace", "workspace_id", "organizations", "orgs"]
            for key in alt_keys:
                if key in auth_json:
                    print(f"[*] Found alternative key '{key}': {auth_json[key]}")
            return None
        workspace_id = str((workspaces[0] or {}).get("id") or "").strip()
        if not workspace_id:
            print("[Error] 无法解析 workspace_id")
            return None

        select_body = f'{{"workspace_id":"{workspace_id}"}}'
        select_resp = s.post(
            "https://auth.openai.com/api/accounts/workspace/select",
            headers={
                "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "content-type": "application/json",
            },
            data=select_body,
        )

        if select_resp.status_code != 200:
            print(f"[Error] 选择 workspace 失败，状态码: {select_resp.status_code}")
            print(select_resp.text)
            return None

        continue_url = str((select_resp.json() or {}).get("continue_url") or "").strip()
        if not continue_url:
            print("[Error] workspace/select 响应里缺少 continue_url")
            return None

        current_url = continue_url
        for _ in range(6):
            final_resp = s.get(current_url, allow_redirects=False, timeout=15)
            location = final_resp.headers.get("Location") or ""

            if final_resp.status_code not in [301, 302, 303, 307, 308]:
                break
            if not location:
                break

            next_url = urllib.parse.urljoin(current_url, location)
            if "code=" in next_url and "state=" in next_url:
                return submit_callback_url(
                    callback_url=next_url,
                    code_verifier=oauth.code_verifier,
                    redirect_uri=oauth.redirect_uri,
                    expected_state=oauth.state,
                    account_email=email,
                    account_password=password,
                )
            current_url = next_url

        print("[Error] 未能在重定向链中捕获到最终 Callback URL")
        return None

    except Exception as e:
        print(f"[Error] 运行时发生错误: {e}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAI 自动注册脚本")
    parser.add_argument(
        "--proxy", default=None, help="代理地址，如 http://127.0.0.1:7890"
    )
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument("--sleep-min", type=int, default=5, help="循环模式最短等待秒数")
    parser.add_argument(
        "--sleep-max", type=int, default=30, help="循环模式最长等待秒数"
    )
    args = parser.parse_args()

    sleep_min = max(1, args.sleep_min)
    sleep_max = max(sleep_min, args.sleep_max)

    count = 0
    print("[Info] Yasal's Seamless OpenAI Auto-Registrar Started for ZJH")

    while True:
        count += 1
        print(
            f"\n[{datetime.now().strftime('%H:%M:%S')}] >>> 开始第 {count} 次注册流程 <<<"
        )

        try:
            token_json = run(args.proxy)

            if token_json:
                try:
                    t_data = json.loads(token_json)
                    fname_email = t_data.get("email", "unknown").replace("@", "_")
                except Exception:
                    fname_email = "unknown"

                # 建立 token 資料夾（如果不存在）
                token_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token")
                os.makedirs(token_dir, exist_ok=True)

                file_name = f"token_{fname_email}_{int(time.time())}.json"
                file_path = os.path.join(token_dir, file_name)

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(token_json)

                print(f"[*] 成功! Token 已保存至: {file_path}")
            else:
                print("[-] 本次注册失败。")

        except Exception as e:
            print(f"[Error] 发生未捕获异常: {e}")

        if args.once:
            break

        wait_time = random.randint(sleep_min, sleep_max)
        print(f"[*] 休息 {wait_time} 秒...")
        time.sleep(wait_time)


if __name__ == "__main__":
    main()
