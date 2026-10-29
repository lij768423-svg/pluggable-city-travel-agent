// 选城页 12 城卡片：名称、代表景点与配图（本机 ComfyUI Qwen-Image 2.1 生成的石膏微缩模型静物，见 scripts/gen_city_photos.py）。
// 是否可选以后端 /api/meta 的启用城市为准；未启用的显示“即将开放”。新城市接入后在此登记一行即可。
import hangzhou from './assets/city-photos/hangzhou.webp'
import nanjing from './assets/city-photos/nanjing.webp'
import suzhou from './assets/city-photos/suzhou.webp'
import beijing from './assets/city-photos/beijing.webp'
import shanghai from './assets/city-photos/shanghai.webp'
import guangzhou from './assets/city-photos/guangzhou.webp'
import shenzhen from './assets/city-photos/shenzhen.webp'
import chengdu from './assets/city-photos/chengdu.webp'
import chongqing from './assets/city-photos/chongqing.webp'
import wuhan from './assets/city-photos/wuhan.webp'
import xian from './assets/city-photos/xian.webp'
import xiamen from './assets/city-photos/xiamen.webp'
export interface CityShowcase { key: string; name: string; landmark: string; image: string }
export const cityShowcase: CityShowcase[] = [
  { key: 'hangzhou', name: '杭州', landmark: '西湖 · 雷峰塔', image: hangzhou },
  { key: 'nanjing', name: '南京', landmark: '明城墙 · 中华门', image: nanjing },
  { key: 'suzhou', name: '苏州', landmark: '园林 · 月洞门', image: suzhou },
  { key: 'beijing', name: '北京', landmark: '天坛 · 祈年殿', image: beijing },
  { key: 'shanghai', name: '上海', landmark: '陆家嘴 · 东方明珠', image: shanghai },
  { key: 'guangzhou', name: '广州', landmark: '珠江 · 广州塔', image: guangzhou },
  { key: 'shenzhen', name: '深圳', landmark: '福田 · 平安金融中心', image: shenzhen },
  { key: 'chengdu', name: '成都', landmark: '熊猫 · 川西院落', image: chengdu },
  { key: 'chongqing', name: '重庆', landmark: '嘉陵江 · 洪崖洞', image: chongqing },
  { key: 'wuhan', name: '武汉', landmark: '蛇山 · 黄鹤楼', image: wuhan },
  { key: 'xian', name: '西安', landmark: '大慈恩寺 · 大雁塔', image: xian },
  { key: 'xiamen', name: '厦门', landmark: '鼓浪屿 · 日光岩', image: xiamen },
]
