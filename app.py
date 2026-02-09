import streamlit as st
import folium
from streamlit_folium import st_folium
import geemap
import ee
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
import json

# --- 1. セキュリティ設定（Earth Engine 認証） ---
def authenticate_ee():
    if "ee_initialized" not in st.session_state:
        try:
            # Secretsから取得
            ee_key_dict = json.loads(st.secrets["earth_engine_json"])
            credentials = ee.ServiceAccountCredentials(
                ee_key_dict['client_email'], 
                key_data=json.dumps(ee_key_dict)
            )
            ee.Initialize(credentials, project=st.secrets["project_id"])
            st.session_state.ee_initialized = True
        except Exception as e:
            st.error(f"認証に失敗しました。: {e}")
            st.stop()

authenticate_ee()

# --- 2. 画面構成 ---
st.set_page_config(page_title="NDVI時系列解析ツール", layout="wide")

st.title("🛰 任意範囲のNDVI時系列解析ツール")
st.markdown("""
名古屋市の現場調査を支援するための、衛星データ活用プロトタイプです。  
地図上のツール（四角や多角形）で範囲を選択してください。
""")

with st.sidebar:
    st.header("解析設定")
    analysis_years = st.slider("解析年数 (過去)", 1, 5, 3)
    st.info("1. 左のツールバーで範囲を囲む\n2. 自動的に解析が始まります")

# --- 3. 地図の表示（安定版実装） ---
# 1. 純粋な folium でベースマップを作成
m = folium.Map(location=[35.181, 136.906], zoom_start=14)

# 2. Google Earth Engine の衛星写真レイヤーを手動で追加
# GEEのTile URLを取得するヘルパー関数
def add_ee_layer(self, ee_image_object, vis_params, name):
    map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
    folium.raster_layers.TileLayer(
        tiles=map_id_dict['tile_fetcher'].url_format,
        attr='Google Earth Engine',
        name=name,
        overlay=True,
        control=True
    ).add_to(self)

# folium.MapにGEEレイヤー追加機能を持たせる
folium.Map.add_ee_layer = add_ee_layer

# ハイブリッド表示（衛星写真）の追加
# 衛星写真の背景として Google のタイル等を表示したい場合は以下のように記述
folium.TileLayer(
    tiles='https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
    attr='Google',
    name='Google Satellite',
    overlay=False,
    control=True
    max_zoom=22,
    max_native_zoom=18
).add_to(m)

# 描画コントロールを追加（範囲選択用）
from folium.plugins import Draw
Draw(export=True).add_to(m)

# 地図を表示
map_data = st_folium(
    m, 
    height=600, 
    width=800,
    use_container_width=True,
    key="main_map",
    returned_objects=["last_active_drawing"]
)

# --- 4. 解析ロジック ---
# map_data が辞書型であることを確認して処理を開始
if isinstance(map_data, dict) and map_data.get("last_active_drawing"):
    st.divider()
    # (以下、解析ロジックは変更なし)
    with st.spinner("衛星データを解析中..."):
        try:
            # 描画図形の取得
            geo_json = map_data["last_active_drawing"]
            geom = ee.Geometry(geo_json['geometry'])
            
            # 中心点の取得（Google Mapリンク用）
            centroid = geom.centroid().coordinates().getInfo()
            lon, lat = centroid[0], centroid[1]

            # 期間設定
            end_date = ee.Date(datetime.now().strftime('%Y-%m-%d'))
            start_date = end_date.advance(-analysis_years, 'year')

            # Sentinel-2 データ抽出
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
            
            # データの取得
            raw_data = processed_col.reduceColumns(ee.Reducer.toList(2), ['system:time_start', 'NDVI_mean']).get('list').getInfo()

            if not raw_data:
                st.warning("指定された範囲・期間内に、雲の少ない有効な衛星データが見つかりませんでした。")
            else:
                df = pd.DataFrame(raw_data, columns=['Timestamp', 'NDVI'])
                df['Date'] = pd.to_datetime(df['Timestamp'], unit='ms')
                df = df.sort_values('Date')

                # 結果表示レイアウト
                col1, col2 = st.columns([1, 2])
                
                with col1:
                    st.success("解析完了")
                    st.metric("データ取得数", f"{len(df)} 件")
                    gmap_url = f"https://www.google.com/maps?q={lat},{lon}"
                    st.markdown(f'### [📍 Google Mapで現地を確認]({gmap_url})')

                with col2:
                    # グラフ描画
                    fig, ax = plt.subplots(figsize=(10, 5))
                    ax.plot(df['Date'], df['NDVI'], marker='o', markersize=4, color='#2ecc71', linestyle='-', linewidth=1)
                    ax.axhline(y=0.3, color='#e74c3c', linestyle='--', alpha=0.5, label='閾値 (0.3)')
                    ax.set_title(f"NDVI時系列推移 (過去 {analysis_years} 年間)")
                    ax.set_ylabel("NDVI")
                    ax.set_ylim(-0.1, 1.0)
                    ax.grid(True, alpha=0.2)
                    ax.legend()
                    st.pyplot(fig)

                    # 画像ダウンロード
                    from io import BytesIO
                    buf = BytesIO()
                    fig.savefig(buf, format="png", dpi=150)
                    st.download_button(
                        label="📥 グラフを保存 (Word/報告書用)",
                        data=buf.getvalue(),
                        file_name=f"NDVI_Report_{datetime.now().strftime('%Y%m%d')}.png",
                        mime="image/png"
                    )

        except Exception as e:
            st.error(f"解析エラーが発生しました: {e}")
else:
    # まだ図形が描かれていない時の表示
    st.info("👆 地図左側の「🔲（四角）」または「⬠（多角形）」ツールを選択して、解析したい範囲を囲んでください。")