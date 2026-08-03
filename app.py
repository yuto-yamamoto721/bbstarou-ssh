#!/usr/bin/env python3
import socket
import threading
import sqlite3
import hashlib
from datetime import datetime
import paramiko

DB_PATH = "bbs.db"
PORT = 8080

# 1. RSAKey を使用してメモリ上でホスト鍵を自動生成（ファイル作成不要）
print("Generating in-memory SSH host key...")
HOST_KEY = paramiko.RSAKey.generate(2048)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    # 投稿テーブル
    conn.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            body TEXT,
            is_registered INTEGER DEFAULT 0,
            created_at TEXT
        )
    ''')
    # アカウントテーブル
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT,
            created_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_anonymous_id(ip):
    today = datetime.now().strftime('%Y-%m-%d')
    raw = f"{ip}:{today}:TERMINAL_SECRET_SALT"
    return "ID:" + hashlib.sha256(raw.encode()).hexdigest()[:8]

# 2. SSH認証の無効化（パスワード不要接続）
class GitHubStyleSSHServer(paramiko.ServerInterface):
    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_none(self, username):
        return paramiko.AUTH_SUCCESSFUL

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_SUCCESSFUL

    def check_pty_request(self, channel, term, modes):
        return True

    def check_shell_request(self, channel):
        return True

# 3. 接続クライアントごとのメインUI処理
def handle_ssh_client(client_socket, client_addr):
    ip = client_addr[0]
    anon_id = get_anonymous_id(ip)

    transport = paramiko.Transport(client_socket)
    transport.add_server_key(HOST_KEY)
    server = GitHubStyleSSHServer()

    try:
        transport.start_server(server=server)
    except paramiko.SSHException:
        return

    chan = transport.accept(20)
    if chan is None:
        return

    f_in = chan.makefile('r', buffering=1)
    f_out = chan.makefile('w', buffering=1)

    def write(text):
        f_out.write(text.replace('\n', '\r\n'))
        f_out.flush()

    # セッション状態管理
    logged_user = None
    temp_nick = "名無しさん＠お腹いっぱい。"
    status_msg = ""

    while True:
        # 画面全消去 & カーソルホーム位置移動 (ANSI)
        write("\033[2J\033[H")
        write(f"\033[1;33m=== [2ch SSH Live CMD BBS @ Port {PORT}] ===\033[0m\n\n")

        # 直近20件の書き込みを表示
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, body, is_registered, created_at FROM posts ORDER BY id ASC LIMIT 20")
        rows = cursor.fetchall()
        conn.close()

        for row in rows:
            post_id, name, body, is_reg, created_at = row
            user_tag = "\033[1;35m[REGISTERED]\033[0m" if is_reg else anon_id
            write(f"\033[1;36m{post_id}\033[0m ：\033[1;32m{name}\033[0m：{created_at} {user_tag}\n")
            write(f"   {body.replace('\n', '\n   ')}\n\n")

        write("-" * 75 + "\n")
        
        # ログイン状態の表示
        if logged_user:
            write(f"ステータス: \033[1;32m{logged_user}\033[0m (ログイン中) | /logout\n")
        else:
            write(f"ステータス: ゲスト ({temp_nick}) | /register <user> <pass> | /login <user> <pass>\n")

        if status_msg:
            write(f"\033[1;31m[{status_msg}]\033[0m\n")
            status_msg = ""

        write("メッセージ入力 (空Enterで更新, /qで退出): > ")

        try:
            input_str = f_in.readline()
            if not input_str:
                break
            msg = input_str.strip()
        except Exception:
            break

        # コマンド処理
        if msg == '/q':
            write("\nBye!\n")
            chan.close()
            break

        # アカウント作成 (/register user pass)
        elif msg.startswith('/register '):
            parts = msg.split(maxsplit=2)
            if len(parts) == 3:
                u, p = parts[1], parts[2]
                conn = sqlite3.connect(DB_PATH)
                try:
                    conn.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                                 (u, hash_password(p), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                    conn.commit()
                    logged_user = u
                    status_msg = f"アカウント '{u}' を作成し、ログインしました！"
                except sqlite3.IntegrityError:
                    status_msg = "エラー: そのユーザー名は既に使用されています。"
                finally:
                    conn.close()
            else:
                status_msg = "使用法: /register <ユーザー名> <パスワード>"
            continue

        # ログイン (/login user pass)
        elif msg.startswith('/login '):
            parts = msg.split(maxsplit=2)
            if len(parts) == 3:
                u, p = parts[1], parts[2]
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT password_hash FROM users WHERE username = ?", (u,))
                row = cursor.fetchone()
                conn.close()

                if row and row[0] == hash_password(p):
                    logged_user = u
                    status_msg = f"ログイン成功: ようこそ {u} さん！"
                else:
                    status_msg = "エラー: ユーザー名またはパスワードが正しくありません。"
            else:
                status_msg = "使用法: /login <ユーザー名> <パスワード>"
            continue

        # ログアウト (/logout)
        elif msg == '/logout':
            logged_user = None
            status_msg = "ログアウトしました。"
            continue

        # ゲスト時の名前変更 (/nick name)
        elif msg.startswith('/nick ') and not logged_user:
            new_nick = msg[6:].strip()
            if new_nick:
                temp_nick = new_nick
            continue

        elif msg == '':
            continue

        # 投稿の保存
        author_name = logged_user if logged_user else temp_nick
        is_reg = 1 if logged_user else 0
        now = datetime.now().strftime('%Y/%m/%d(%a) %H:%M:%S')

        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO posts (name, body, is_registered, created_at) VALUES (?, ?, ?, ?)",
                     (author_name, msg, is_reg, now))
        conn.commit()
        conn.close()

def main():
    init_db()
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    server_socket.bind(('0.0.0.0', PORT))
    server_socket.listen(100)
    print(f"SSH 2ch BBS Server running on port {PORT}...")

    while True:
        client_socket, client_addr = server_socket.accept()
        threading.Thread(
            target=handle_ssh_client,
            args=(client_socket, client_addr),
            daemon=True
        ).start()

if __name__ == '__main__':
    main()
