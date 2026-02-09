import streamlit as st
import folium
from streamlit_folium import st_folium
from folium.plugins import Geocoder
import geemap
import ee
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
import json
import matplotlib.font_manager as fm  # ← ここで 'fm' として定義します
import os

# --- 0. 日本語豆腐対策（最新Python対応） ---
import matplotlib
from matplotlib import font_manager
# Linux環境(Streamlit Cloud)で標準的なフォントを指定
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Liberation Sans', 'Bitstream Vera Sans', 'sans-serif']

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

from folium.plugins import Geocoder
Geocoder(
    collapsed=False,          # 最初から検索窓を開いておく場合はFalse
    position='topright', 
    add_marker=True,
    placeholder='住所や施設名で検索'
).add_to(m)

# ハイブリッド表示（衛星写真）の追加
# max_zoom と max_native_zoom を指定することで、ズームしても消えないようにします
folium.TileLayer(
    tiles='https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
    attr='Google',
    name='Google Satellite',
    overlay=False,
    control=True,
    max_zoom=22,         # 地図としてズーム可能な最大値
    max_native_zoom=18   # Google衛星写真タイルが存在する最大ズームレベル（これ以上は引き伸ばして表示）
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

# --- 4. 解析ロジック内、グラフ描画部分 ---
                with col2:
                    font_path = 'fonts/NotoSansJP-Regular.ttf'
                    jp_font = None
                    jp_font_bold = None

                    if os.path.exists(font_path):
                        try:
                            # 太字設定をデフォルトに
                            jp_font = fm.FontProperties(fname=font_path, weight='bold')
                            jp_font_bold = fm.FontProperties(fname=font_path, weight='bold')
                        except Exception as e:
                            st.error(f"フォント読み込みエラー: {e}")
                    
                    # --- 完全な黒と太い線の設定 ---
                    pure_black = 'black'
                    plt.rcParams.update({
                        'text.color': pure_black,
                        'axes.labelcolor': pure_black,
                        'axes.edgecolor': pure_black,
                        'xtick.color': pure_black,
                        'ytick.color': pure_black,
                        'axes.labelweight': 'bold',
                        'axes.linewidth': 2.0      # 枠線をかなり太く
                    })

                    fig, ax = plt.subplots(figsize=(10, 5))
                    
                    # プロット自体の線も太くして存在感を出す
                    ax.plot(df['Date'], df['NDVI'], marker='o', markersize=6, color='#2ecc71', linestyle='-', linewidth=2.5)
                    
                    # 閾値の線（赤色を濃く、透過なしに）
                    ax.axhline(y=0.3, color='#ff0000', linestyle='--', alpha=1.0, linewidth=2,
                               label='閾値 (0.3)' if jp_font else 'Threshold (0.3)')
                    
                    # タイトル（最大サイズ + 太字）
                    ax.set_title(f"NDVI時系列推移 (過去 {analysis_years} 年間)" if jp_font else f"NDVI Time Series", 
                                 fontproperties=jp_font_bold, fontsize=16, pad=20)
                    
                    # 軸ラベル（サイズアップ + 太字）
                    ax.set_ylabel("NDVI", fontproperties=jp_font_bold, fontsize=13)
                    ax.set_xlabel("日付" if jp_font else "Date", fontproperties=jp_font_bold, fontsize=13)
                    
                    # 目盛り数字をすべて太字・黒に
                    ax.tick_params(axis='both', which='major', labelsize=11, width=2.0)
                    for tick in ax.get_xticklabels():
                        tick.set_fontproperties(jp_font_bold)
                    for tick in ax.get_yticklabels():
                        tick.set_fontproperties(jp_font_bold)

                    # 凡例（枠も黒く太く）
                    if jp_font:
                        leg = ax.legend(prop=jp_font_bold, frameon=True, loc='upper right')
                        leg.get_frame().set_edgecolor(pure_black)
                        leg.get_frame().set_linewidth(1.5)
                    else:
                        ax.legend()
                    
                    ax.set_ylim(-0.1, 1.0)
                    ax.grid(True, linestyle='-', alpha=0.3, color='#888888') # グリッドは実線にして見やすく

                    st.pyplot(fig)

                    # 画像ダウンロード（DPIを300に上げて高精細に）
                    from io import BytesIO
                    buf = BytesIO()
                    fig.savefig(buf, format="png", dpi=300, bbox_inches='tight')
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