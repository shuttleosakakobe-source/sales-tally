"""売上集計アプリ共通のヘルパー・定数。
顧客マスター・ユーザーマスターはkensaku（メンテナンス依頼アプリ）・campaign-tally（キャンペーン集計アプリ）
と同じスプレッドシートを参照する（拠点・ログイン情報を3アプリで共通化するため）。"""
import streamlit as st
import pandas as pd
import requests
import json
from datetime import timezone, timedelta, datetime

# 顧客マスター（A=加盟店名, B=顧客コード, C=顧客名, D=(未使用), E=加盟店コード）。
# kensaku・campaign-tallyと全く同じマスタを参照する。
BRANCH_CUSTOMER_CSV = {
    "神戸中央店": "https://docs.google.com/spreadsheets/d/1AkMb1J2m3VZAIyMCKmr3T3E8-kJB0BDDdWQJuEn7YGc/gviz/tq?tqx=out:csv&gid=127347205",
    "京都中央店": "https://docs.google.com/spreadsheets/d/1AkMb1J2m3VZAIyMCKmr3T3E8-kJB0BDDdWQJuEn7YGc/gviz/tq?tqx=out:csv&gid=291761179",
    "大阪中央店": "https://docs.google.com/spreadsheets/d/1AkMb1J2m3VZAIyMCKmr3T3E8-kJB0BDDdWQJuEn7YGc/gviz/tq?tqx=out:csv&gid=788868615",
    "大阪北店": "https://docs.google.com/spreadsheets/d/1AkMb1J2m3VZAIyMCKmr3T3E8-kJB0BDDdWQJuEn7YGc/gviz/tq?tqx=out:csv&gid=734014298",
}

# 💡【要設定】GAS Webアプリをデプロイしたあと、発行されたURL（.../exec）に書き換えてください。
GAS_URL = "★★ここに売上集計用GAS WebアプリのURLを貼り付けてください（例: https://script.google.com/macros/s/XXXXXXXX/exec）★★"

# 売上集計データ（セリング・増加・今スト申請、集計表、ランキング表を1つのスプレッドシートにまとめる）
SALES_SPREADSHEET_ID = "1XXQyXibs_beJ3HmrEJIrqq8pAkG0xAeSH_aiRnFn9cA"
SALES_SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SALES_SPREADSHEET_ID}/edit?gid=0#gid=0"
SALES_SHEET_CSV = f"https://docs.google.com/spreadsheets/d/{SALES_SPREADSHEET_ID}/gviz/tq?tqx=out:csv&gid=0"

# CSV一括インポート先（売上データシート）
SALES_IMPORT_SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SALES_SPREADSHEET_ID}/edit?gid=914540555#gid=914540555"
SALES_IMPORT_SHEET_CSV = f"https://docs.google.com/spreadsheets/d/{SALES_SPREADSHEET_ID}/gviz/tq?tqx=out:csv&gid=914540555"

JST = timezone(timedelta(hours=+9), 'JST')


def get_current_role():
    """ログイン中のユーザーの権限（app.pyのログイン処理でユーザーマスターF列から
    取得され st.session_state["user_role"] にセットされたもの）を返す。
    シート側の読み込み方によっては数値列が"2.0"のような文字列になってしまう
    ことがあるため、念のため前後の空白除去と末尾".0"の除去で正規化する。
    （kensaku・campaign-tallyと共通の実装・命名。
    未ログイン等で値が無い場合のデフォルトは、承認・金額を扱うため安全側＝権限なし（""）のままにしている。）"""
    role = str(st.session_state.get("user_role", "")).strip()
    if role.endswith(".0"):
        role = role[:-2]
    return role


def post_to_gas(payload):
    if not GAS_URL or GAS_URL.startswith("★"):
        return {"status": "error", "message": "保存先が未設定です（GAS_URLが空）。管理者に設定を依頼してください。"}
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(GAS_URL, data=json.dumps(payload), headers=headers, timeout=30)
        return response.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}


@st.cache_data(ttl=15)
def read_csv_cached(url, **kwargs):
    """Google SheetsのCSVをキャッシュ付きで読み込む共通ヘルパー（15秒キャッシュ）。"""
    return pd.read_csv(url, dtype=str, **kwargs)


def lookup_customer(cust_code):
    """顧客コードから、ログイン中ユーザーの拠点に対応する顧客マスターを検索し、
    顧客名・加盟店名・加盟店コードを返す。
    戻り値: {"store_name":..., "cust_name":..., "store_code":...} / 見つからなければ None
    （拠点が未ログイン・未設定・対応表に無い場合もNoneを返す）"""
    if not cust_code or not str(cust_code).strip():
        return None

    branch = st.session_state.get("user_branch", "")
    customer_master_csv = BRANCH_CUSTOMER_CSV.get(str(branch).strip())
    if not customer_master_csv:
        return None

    try:
        df_master = read_csv_cached(customer_master_csv, storage_options={"User-Agent": "Mozilla/5.0"})
    except Exception:
        return None

    matched = df_master[df_master.iloc[:, 1].astype(str).str.strip() == str(cust_code).strip()]
    if matched.empty:
        return None

    last_row = matched.iloc[-1]
    return {
        "store_name": str(last_row.iloc[0]) if pd.notna(last_row.iloc[0]) else "",
        "cust_name": str(last_row.iloc[2]) if pd.notna(last_row.iloc[2]) else "",
        "store_code": str(last_row.iloc[4]) if pd.notna(last_row.iloc[4]) else "",
    }
