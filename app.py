import sys
from pathlib import Path

# ルートディレクトリを検索パスに追加（ImportError防止）
sys.path.append(str(Path(__file__).parent))

import streamlit as st
import os
from utils import inject_pwa_blocker, set_login_storage, check_session_storage, clear_login_storage, remember_email, get_remembered_email
from data_loader import load_sheet_data
from views.sales_view import sales_screen

# --- 1. ページ基本設定 ---
st.set_page_config(
    page_title="ダスキンシャトル 売上集計アプリ",
    page_icon="icon.png",
    layout="wide"
)

# --- 2. セッション状態の初期化 ---
if 'login_status' not in st.session_state: st.session_state.login_status = False
if 'logout_requested' not in st.session_state: st.session_state.logout_requested = False

# --- 3. セッションストレージによる自動ログイン確認 ---
if not st.session_state.login_status and not st.session_state.logout_requested:
    check_session_storage()

# --- 4. 画面表示 ---
if st.session_state.login_status:
    with st.sidebar:
        st.write(f"👤 ログイン中: **{st.session_state.get('user_name', '担当者')}**")
        st.caption(f"権限: {st.session_state.get('user_role', 'なし')}")
        if st.button("🚪 ログアウト", use_container_width=True):
            st.session_state.login_status = False
            st.session_state.logout_requested = True
            clear_login_storage()
            st.rerun()

    sales_screen()
else:
    # --- 🔑 ログイン画面 ---
    inject_pwa_blocker()

    col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
    with col_l2:
        if os.path.exists("1.png"):
            st.image("1.png", use_container_width=True)

        pending = st.session_state.get("pending_login")

        if pending:
            # 💡 同じメールアドレス・パスワードの行が複数あり、拠点（B列）が2つ以上ある場合は
            #    ログイン確定前に、どの拠点として使うかを選ばせる
            #    （拠点によって参照する顧客マスターのシートが変わるため）。
            st.info(f"👤 {pending['name']} さん、担当する拠点を選択してください。")
            chosen_branch = st.selectbox("拠点", pending["branches"], key="login_branch_choice")
            if st.button("この拠点で開始", type="primary", use_container_width=True):
                chosen_row = next(
                    (m for m in pending["matched_rows"] if m["branch"] == chosen_branch),
                    pending["matched_rows"][0],
                )
                st.session_state.user_name = chosen_row["name"]
                st.session_state.user_role = chosen_row["role"]
                st.session_state.user_code = chosen_row["email"]
                st.session_state.user_branch = chosen_row["branch"]
                st.session_state.user_area = chosen_row["area"]
                st.session_state.login_status = True
                st.session_state.logout_requested = False
                st.session_state.pop("pending_login", None)

                set_login_storage(
                    st.session_state.user_name, "", False,
                    st.session_state.user_role, st.session_state.user_code,
                    st.session_state.user_branch, st.session_state.user_area,
                    remember=pending.get("remember", False),
                )
                st.rerun()
        else:
            u_email = st.text_input("メールアドレス", value=get_remembered_email()).strip()
            u_pass = st.text_input("パスワード", type="password").strip()
            remember = st.checkbox("📌 ログイン情報を保存する（30日間）")

            if st.button("ログイン", type="primary", use_container_width=True):
                if not u_email or not u_pass:
                    st.error("メールアドレスとパスワードを入力してください")
                    st.stop()
                raw = load_sheet_data(gid="0")
                if raw and len(raw) > 1:
                    # 行ごとに判定 (A列: 0[メール], B列: 1[拠点], C列: 2[名前], D列: 3[パスワード],
                    # F列: 5[権限], G列: 6[エリア])
                    # 同じメール・パスワードの行が複数あってもよい（拠点違いで複数行に分けている場合）ため、
                    # 一致した行すべてから拠点を集める。
                    matched_rows = []
                    for row in raw[1:]:
                        if len(row) >= 6:
                            email_val = str(row[0]).strip()  # A列
                            pass_val = str(row[3]).strip()   # D列
                            if email_val and pass_val and email_val.lower() == u_email.lower() and pass_val == u_pass:
                                matched_rows.append({
                                    "email": email_val,
                                    "branch": str(row[1]).strip(),  # B列
                                    "name": str(row[2]).strip(),    # C列
                                    "role": str(row[5]).strip(),    # F列
                                    "area": str(row[6]).strip() if len(row) >= 7 else "",  # G列
                                })

                    if matched_rows:
                        remember_email(u_email)
                        branches = list(dict.fromkeys(m["branch"] for m in matched_rows if m["branch"]))
                        first = matched_rows[0]

                        if len(branches) <= 1:
                            st.session_state.user_name = first["name"]
                            st.session_state.user_role = first["role"]
                            st.session_state.user_code = first["email"]
                            st.session_state.user_branch = branches[0] if branches else ""
                            st.session_state.user_area = first["area"]
                            st.session_state.login_status = True
                            st.session_state.logout_requested = False

                            set_login_storage(
                                st.session_state.user_name, "", False,
                                st.session_state.user_role, st.session_state.user_code,
                                st.session_state.user_branch, st.session_state.user_area,
                                remember=remember,
                            )
                        else:
                            st.session_state.pending_login = {
                                "email": first["email"],
                                "name": first["name"],
                                "role": first["role"],
                                "branches": branches,
                                "matched_rows": matched_rows,
                                "remember": remember,
                            }
                        st.rerun()
                    else:
                        st.error("認証失敗: メールアドレスまたはパスワードが正しくありません")
                else:
                    st.error("マスターデータの読み込みに失敗しました。シートの共有設定（アクセス権限）を確認してください。")
