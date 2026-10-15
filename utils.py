import json
import time
import streamlit as st

try:
    from streamlit_local_storage import LocalStorage
except Exception:
    # 💡 streamlit_local_storageのimportに失敗した場合（Streamlit Cloud側の
    # インストール不具合など）でも、アプリ全体がImportErrorで落ちないようにする。
    # 以下の各関数は元々try/exceptでLocalStorage関連の失敗を握っているため、
    # ここでNoneにしておけば「ログイン記憶」機能だけが無効になり、通常ログインは動作する。
    LocalStorage = None

# ブラウザのlocalStorageに、ログイン記憶情報を保存するキー名
_LOGIN_STORAGE_KEY = "campaign_tally_login_info"
# 再ログイン画面でメールアドレスを自動入力するための保存キー（ログアウトしても消さない）
_LOGIN_EMAIL_KEY = "campaign_tally_login_email"
# 「ログイン情報を保存する」を選んだ場合の保存期間（30日）
_LOGIN_STORAGE_TTL_SECONDS = 30 * 24 * 60 * 60


def inject_pwa_blocker():
    """PWAブロック用のJavaScriptスクリプト注入（現状は未使用）"""
    pass


def _get_local_storage():
    """LocalStorageコンポーネントのインスタンスをsession_stateにキャッシュして使い回す
    （毎回new LocalStorage()すると、ブラウザとの同期が走り直してしまうため）。
    （kensaku: utils.py と共通の実装。将来1つの業務アプリに統合する際、
    ログイン記憶の仕組みをそのまま流用できるようにしている。）"""
    if LocalStorage is None:
        raise RuntimeError("streamlit_local_storage is unavailable")
    if "_local_storage" not in st.session_state:
        st.session_state["_local_storage"] = LocalStorage()
    return st.session_state["_local_storage"]


def set_login_storage(user_name, user_url, needs_alert, user_role, user_code, user_branch="", user_area="", remember=False):
    """ログイン情報をセッション状態に保存する。今回のログイン自体はこれで完了する
    （remember の値に関わらず、ログイン中は使える）。
    💡 remember=True のときだけ、ブラウザのlocalStorageにも保存し、ブラウザを閉じて
    開き直しても30日間は再ログインしなくて済むようにする（「ログイン情報を保存する」
    ボタン／チェックボックスを押した場合のみ）。remember=False の場合は、以前保存されて
    いた情報があれば念のため消しておく（チェックを外して以前保存した情報が残り続ける
    事態を防ぐため）。
    パスワードは一切保存しない。保存するのはログイン状態を復元するための
    最小限の情報（氏名・権限・メール・拠点・エリア・保存日時）のみ。
    （kensaku: utils.py と共通の実装。）"""
    st.session_state["user_name"] = user_name
    st.session_state["user_url"] = user_url
    st.session_state["needs_alert"] = needs_alert
    st.session_state["user_role"] = user_role
    st.session_state["user_code"] = user_code
    st.session_state["user_branch"] = user_branch
    st.session_state["user_area"] = user_area

    if not remember:
        clear_login_storage()
        return

    try:
        _get_local_storage().setItem(_LOGIN_STORAGE_KEY, json.dumps({
            "user_name": user_name,
            "user_role": user_role,
            "user_code": user_code,
            "user_branch": user_branch,
            "user_area": user_area,
            "saved_at": time.time(),
        }))
        # 💡 setItem直後にページが切り替わる（st.rerun()される）と、ブラウザ側が
        #    localStorageへの書き込みを終える前にコンポーネントが外れてしまい、
        #    保存されないことがある（streamlit-local-storageの既知の挙動）。
        #    特にiPad（モバイルSafari）ではコンポーネントの読み込み・応答が
        #    PCより遅くなりやすく、0.5秒では書き込みが終わらないことがあったため、
        #    余裕を持って1.5秒に伸ばしている。
        time.sleep(1.5)
    except Exception:
        # localStorageへの保存に失敗しても、今回のログイン自体（session_state）は
        # 既に完了しているので、アプリの動作は止めない（次回また手入力ログインになるだけ）。
        pass


def check_session_storage():
    """ブラウザのlocalStorageに記憶されたログイン情報があれば、ログイン画面を
    飛ばして自動的にログイン状態を復元する。パスワードの再検証はしない
    （そもそも保存していないため）。保存から30日を超えている場合は期限切れとして
    情報を消し、普通にログインし直してもらう。ログアウト時は必ず
    clear_login_storage() で消すこと（消さないとログアウトしてもすぐ
    自動ログインし直されてしまう）。"""
    try:
        raw = _get_local_storage().getItem(_LOGIN_STORAGE_KEY)
    except Exception:
        return

    if not raw:
        return
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return
    if not isinstance(raw, dict) or not raw.get("user_code"):
        return

    saved_at = raw.get("saved_at")
    try:
        is_expired = saved_at is None or (time.time() - float(saved_at)) > _LOGIN_STORAGE_TTL_SECONDS
    except (TypeError, ValueError):
        is_expired = True
    if is_expired:
        clear_login_storage()
        return

    st.session_state["user_name"] = raw.get("user_name", "")
    st.session_state["user_role"] = raw.get("user_role", "")
    st.session_state["user_code"] = raw.get("user_code", "")
    st.session_state["user_branch"] = raw.get("user_branch", "")
    st.session_state["user_area"] = raw.get("user_area", "")
    st.session_state["login_status"] = True


def clear_login_storage():
    """ログアウト時に、記憶していたログイン情報をブラウザのlocalStorageから消す。
    （メールアドレスの記憶＝remember_email は、再ログイン時の入力の手間を省くための
    別機能なので、ここでは消さない。）"""
    try:
        _get_local_storage().deleteItem(_LOGIN_STORAGE_KEY)
    except Exception:
        pass


def remember_email(email):
    """次回ログイン画面を開いたときにメールアドレスを自動入力できるよう、
    ブラウザのlocalStorageに保存する。ログアウトしても消さない
    （ログイン状態の保持＝set_login_storageとは別の、入力の手間を省くための機能）。
    パスワードは保存しない。
    （kensaku: utils.py と共通の実装。）"""
    try:
        _get_local_storage().setItem(_LOGIN_EMAIL_KEY, email)
        time.sleep(1.5)  # set_login_storageと同じ理由（書き込みが終わる前にページが切り替わるのを防ぐ）
    except Exception:
        pass


def get_remembered_email():
    """保存されているメールアドレスを取得する（無ければ空文字）。"""
    try:
        raw = _get_local_storage().getItem(_LOGIN_EMAIL_KEY)
    except Exception:
        return ""
    return raw if isinstance(raw, str) else ""
