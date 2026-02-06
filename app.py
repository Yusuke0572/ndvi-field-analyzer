import streamlit as st
import geemap
import ee
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
import base64
from io import BytesIO
import json

# --- 1. セキュリティ設定（Earth Engine 認証） ---
def authenticate_ee():
    """Streamlit Secretsからサービスアカウント情報を読み取り認証する"""
    try:
        # StreamlitのSecrets管理画面に貼り付けたJSONを読み込む
        # 後ほど設定する名前: "earth_engine_key"
        ee_key_dict = json.loads(st.secrets["earth_engine_json"])
        credentials = ee.ServiceAccountCredentials(ee_key_dict['client_email'], key_data=json.dumps(ee_key_dict))
        ee.Initialize(credentials, project=st.secrets["project_id"])
    except Exception as e:
        st.error(f"認証に失敗しました。管理者へ連絡してください。 Error: {e}")
        st.stop()

authenticate_ee()

# --- 2. 画面構成 ---
st.set_page_config(page_title="NDVI時系列解析ツール", layout="wide")

st.title("🛰 任意範囲のNDVI時系列解析ツール")
st.markdown("""
名古屋市の現場調査を支援するための、衛星データ活用プロトタイプです。  
地図上で範囲を囲むと、過去の植生指数（NDVI）の推移をグラフ化します。
""")

# サイドバー設定
with st.sidebar:
    st.header("解析設定")
    analysis_years = st.slider("解析年数 (過去)", 1, 5, 3)
    st.info("地図上の「四角」や「多角形」ツールで範囲を選択してください。")

# グラフ出力用コンテナ
chart_container = st.container()

# --- 3. 地図の表示 ---
m = geemap.Map(center=[35.181, 136.906], zoom=14)
m.add_basemap('HYBRID')

# Streamlitで地図を表示（描画コントロールを有効化）
# 地図を操作すると、このコードが再実行され、データの更新を検知します
map_data = m.to_streamlit(height=600)

# --- 4. 解析ロジック ---
# 最後に描画された図形があるか確認
if map_data.get("last_active_drawing"):
    with chart_container:
        with st.spinner("衛星データを解析中..."):
            try:
                # 描画図形の取得
                geo_json = map_data["last_active_drawing"]
                geom = ee.Geometry(geo_json['geometry'])
                centroid = geom.centroid().coordinates().getInfo()
                lon, lat = centroid[0], centroid[1]

                # 期間設定
                end_date = ee.Date(datetime.now().strftime('%Y-%m-%d'))
                start_date = end_date.advance(-analysis_years, 'year')

                # データ抽出
                s2_col = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                    .filterBounds(geom) \
                    .filterDate(start_date, end_date) \
                    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))

                def get_area_stats(img):
                    ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
                    stats = ndvi.reduceRegion(
                        reducer=ee.Reducer.mean(),
                        geometry=geom,
                        scale=10,
                        maxPixels=1e8
                    )
                    return img.set('system:time_start', img.get('system:time_start')).set('NDVI_mean', stats.get('NDVI'))

                processed_col = s2_col.map(get_area_stats).filter(ee.Filter.notNull(['NDVI_mean']))
                data_list = processed_col.reduceColumns(ee.Reducer.toList(2), ['system:time_start', 'NDVI_mean']).get('list').getInfo()

                if not data_list:
                    st.warning("指定された範囲に有効な衛星データが見つかりませんでした。")
                else:
                    # データ加工
                    df = pd.DataFrame(data_list, columns=['Timestamp', 'NDVI'])
                    df['Date'] = pd.to_datetime(df['Timestamp'], unit='ms')
                    df = df.sort_values('Date')

                    # リンク表示
                    gmap_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
                    st.markdown(f'[📍 Google Mapで現地を確認する]({gmap_url})')

                    # グラフ描画
                    fig, ax = plt.subplots(figsize=(10, 4))
                    ax.plot(df['Date'], df['NDVI'], marker='o', color='#2ecc71', alpha=0.8)
                    ax.axhline(y=0.3, color='#e74c3c', linestyle='--', alpha=0.5, label='Threshold (0.3)')
                    ax.set_title(f"NDVI Trend (Past {analysis_years} Years)")
                    ax.set_ylim(-0.1, 1.0)
                    ax.grid(True, alpha=0.3)
                    ax.legend()
                    st.pyplot(fig)

                    # Word用ダウンロードボタン
                    tmp_img = BytesIO()
                    plt.savefig(tmp_img, format='png', bbox_inches='tight', dpi=150)
                    st.download_button(
                        label="📥 グラフを画像として保存 (Word用)",
                        data=tmp_img.getvalue(),
                        file_name=f"NDVI_Report_{datetime.now().strftime('%Y%m%d')}.png",
                        mime="image/png"
                    )

            except Exception as e:
                st.error(f"解析エラー: {e}")