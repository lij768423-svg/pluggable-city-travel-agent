// 问卷与登录页两侧的城市背景图：石膏建筑微缩模型、纯白底、地标沿外侧边缘堆叠（scripts/gen_questionnaire_art.py，
// 本机 ComfyUI 在 RTX 3080 上生成，2026-10-23 替换原先三城的 SVG 插画）。以图片引用、带内容哈希，只加载当前城市两张。
// 新增城市在此登记，空缺时用中性底纹。
import hangzhouLeft from './assets/questionnaire-art/hangzhou-left.webp'
import hangzhouRight from './assets/questionnaire-art/hangzhou-right.webp'
import nanjingLeft from './assets/questionnaire-art/nanjing-left.webp'
import nanjingRight from './assets/questionnaire-art/nanjing-right.webp'
import suzhouLeft from './assets/questionnaire-art/suzhou-left.webp'
import suzhouRight from './assets/questionnaire-art/suzhou-right.webp'
import beijingLeft from './assets/questionnaire-art/beijing-left.webp'
import beijingRight from './assets/questionnaire-art/beijing-right.webp'
import shanghaiLeft from './assets/questionnaire-art/shanghai-left.webp'
import shanghaiRight from './assets/questionnaire-art/shanghai-right.webp'
import guangzhouLeft from './assets/questionnaire-art/guangzhou-left.webp'
import guangzhouRight from './assets/questionnaire-art/guangzhou-right.webp'
import shenzhenLeft from './assets/questionnaire-art/shenzhen-left.webp'
import shenzhenRight from './assets/questionnaire-art/shenzhen-right.webp'
import chengduLeft from './assets/questionnaire-art/chengdu-left.webp'
import chengduRight from './assets/questionnaire-art/chengdu-right.webp'
import chongqingLeft from './assets/questionnaire-art/chongqing-left.webp'
import chongqingRight from './assets/questionnaire-art/chongqing-right.webp'
import wuhanLeft from './assets/questionnaire-art/wuhan-left.webp'
import wuhanRight from './assets/questionnaire-art/wuhan-right.webp'
export interface CityArtwork { left: string; right: string; leftLabel: string; rightLabel: string }
const artworks: Record<string, CityArtwork> = {
  hangzhou: { left: hangzhouLeft, right: hangzhouRight, leftLabel: '保俶塔与断桥', rightLabel: '雷峰塔与龙井茶田' },
  nanjing: { left: nanjingLeft, right: nanjingRight, leftLabel: '中华门与梧桐', rightLabel: '中山陵与秦淮河' },
  suzhou: { left: suzhouLeft, right: suzhouRight, leftLabel: '园林月洞', rightLabel: '虎丘塔与水巷' },
  beijing: { left: beijingLeft, right: beijingRight, leftLabel: '故宫角楼与胡同', rightLabel: '长城、天坛与十七孔桥' },
  shanghai: { left: shanghaiLeft, right: shanghaiRight, leftLabel: '外滩与外白渡桥', rightLabel: '陆家嘴' },
  guangzhou: { left: guangzhouLeft, right: guangzhouRight, leftLabel: '广州塔与珠江', rightLabel: '陈家祠与沙面' },
  shenzhen: { left: shenzhenLeft, right: shenzhenRight, leftLabel: '平安金融中心与深圳湾', rightLabel: '大鹏所城与海岸' },
  chengdu: { left: chengduLeft, right: chengduRight, leftLabel: '宽窄巷子与熊猫', rightLabel: '武侯祠与安顺廊桥' },
  chongqing: { left: chongqingLeft, right: chongqingRight, leftLabel: '洪崖洞与千厮门大桥', rightLabel: '解放碑与长江索道' },
  wuhan: { left: wuhanLeft, right: wuhanRight, leftLabel: '黄鹤楼与长江大桥', rightLabel: '武大樱花与东湖' },
}
export const cityArtwork = (city: string): CityArtwork | undefined => artworks[city]
// 进城过场期间调用：下载并解码两侧图，失败也视为完成（图片自己会淡入或留空）
export function preloadCityArtwork(city: string): Promise<void> {
  const art = artworks[city]
  if (!art) return Promise.resolve()
  return Promise.all([art.left, art.right].map(src => {
    const image = new Image()
    image.src = src
    return image.decode().catch(() => undefined)
  })).then(() => undefined)
}
