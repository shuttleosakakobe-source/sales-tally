import streamlit as st
import pandas as pd

# ユーザーマスター（ダスキンシャトル業務アプリ群共通。kensaku（メンテナンス依頼アプリ）と
# 同じスプレッドシート・同じログイン情報を使う）
USER_MASTER_CSV = "https://docs.google.com/spreadsheets/d/1AkMb1J2m3VZAIyMCKmr3T3E8-kJB0BDDdWQJuEn7YGc/gviz/tq?tqx=out:csv&gid=0"

def load_sheet_data(gid="0"):
    """ユーザーマスターのスプレッドシートからデータを取得
    （dtype=strで読み込むことで、権限列（F列）などの数字だけの列がpandasに
    よって数値型に変換され、"2"が"2.0"のような文字列になってしまうのを防ぐ）
    短時間キャッシュ（15秒）により、ログイン試行のたびにGoogle Sheetsへ
    問い合わせが飛ぶのを防ぐ。"""
    try:
        df = _read_csv_cached_str(USER_MASTER_CSV)
        if df.empty:
            return None
        headers = df.columns.tolist()
        data = df.fillna("").values.tolist()
        return [headers] + data
    except Exception as e:
        print(f"Data loading error: {e}")
        return None


@st.cache_data(ttl=15)
def _read_csv_cached_str(url):
    return pd.read_csv(url, dtype=str)
