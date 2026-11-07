/* MedScribe HK · integration UI (v9 shell, real API)
 * Replaces the static preview state with the integration layer's HTTP API.
 * No mock patient, segment, report or export data is rendered here.
 */
(function () {
  'use strict';

  var THEME_KEY = 'medscribe-theme';
  var LANG_KEY = 'medscribe-lang';
  var ASR_LANG_KEY = 'medscribe-asr-language';
  var root = document.documentElement;
  var media = window.matchMedia('(prefers-color-scheme: dark)');

  var $ = function (id) { return document.getElementById(id); };
  var RANK = {
    created: 0, recorded: 1, transcribed: 2, corrected: 3,
    extracted: 4, generated: 5, signed: 6, exported: 7
  };
  var STEP_FOR_RANK = [0, 1, 2, 3, 3, 4, 5, 5];

  var state = {
    lang: 'zh',
    sessionId: null,
    detail: null,
    sessions: [],
    templates: [],
    models: [],
    asrModel: '',
    asrLanguage: 'auto',
    asrDevice: '',
    filter: 'all',
    exportFormat: 'pdf',
    busy: false,
    recorder: null,
    chunks: [],
    recTimer: null,
    recSeconds: 0,
    playing: false,
    transcribing: false,
    transcribeJob: null,
    pendingAudioName: '',
    extUrl: ''
  };

  /* ------------------------------------------------------------------ i18n */
  var I18N = {
    zh: {
      'session.new': '新建会话',
      'search.placeholder': '搜索患者或会话号',
      'search.aria': '搜索会话',
      'rail.aria': '会话列表',
      'rail.empty': '暂无会话',
      'rail.today': '今天',
      'rail.earlier': '更早',
      'rail.count': '{n} 例',
      'pref.theme': '界面主题',
      'pref.themeAria': '界面主题',
      'pref.language': '界面语言',
      'pref.languageAria': '界面语言',
      'theme.light': '浅色',
      'theme.dark': '深色',
      'theme.system': '系统',
      'theme.switch': '切换主题',
      'language.zh': '简体中文',
      'language.zhHant': '繁體中文',
      'language.en': 'English',
      'language.switch': '切换语言',
      'zoom.hint': 'Ctrl+滚轮以缩放界面',
      'avatar.user': '林',
      'user.name': '林医生',
      'user.role': '内科',
      'patient.empty': '未选择会话',
      'status.created': '已建会话',
      'status.recorded': '已录音',
      'status.transcribed': '已转写',
      'status.corrected': '术语已纠正',
      'status.extracted': '已抽取',
      'status.generated': '待校审',
      'status.signed': '已签署',
      'status.exported': '已导出',
      'stepper.aria': '记录流程',
      'step.record': '录音',
      'step.transcribe': '转写',
      'step.review': '校对',
      'step.extract': '抽取',
      'step.approve': '校审',
      'step.sign': '签署',
      'empty.title': '还没有会话',
      'empty.sub': '新建会话后，按「录音 → 转写 → 术语纠正 → 抽取 → 生成初稿 → 校审 → 签署 → 导出」完成一次病历记录。演示只使用合成语料。',
      'empty.cta': '新建会话',
      'review.title': '转写校对',
      'review.sub': '核对低置信内容并修正文本。报告字段随校审更新；必填项和低置信片段未处理前不可签署。',
      'filters.aria': '片段筛选',
      'filter.all': '全部',
      'filter.low': '低置信',
      'filter.confirmed': '已确认',
      'soap.s': '主观',
      'soap.o': '客观',
      'soap.a': '评估',
      'soap.p': '计划',
      'audio.aria': '音频播放器',
      'audio.play': '播放音频',
      'audio.pause': '暂停音频',
      'audio.speed': '播放速度',
      'audio.record': '录音',
      'audio.stop': '停止',
      'audio.upload': '上传音频',
      'action.template': '模板',
      'action.model': '模型',
      'action.modelAria': '语音识别模型',
      'action.language': '语言',
      'action.languageAria': '识别语言',
      'asr.lang.auto': '自动识别',
      'asr.lang.zh': '中文',
      'asr.lang.yue': '粤语',
      'asr.lang.en': '英语',
      'asr.lang.ja': '日语',
      'asr.lang.ko': '韩语',
      'model.localSuffix': ' · 本地',
      'model.remoteSuffix': ' · 未安装',
      'model.switching': '正在切换模型：{name}（首次使用需加载模型，可能耗时数十秒）…',
      'model.switched': '已切换到 {name}（{s}s）',
      'action.transcribe': '转写',
      'action.transcribing': '转写中…',
      'action.correct': '术语纠正',
      'action.undo': '撤销纠正',
      'action.extract': '抽取 S/O/A/P',
      'action.generate': '生成初稿',
      'transcript.title': '转写原文',
      'transcript.empty': '尚无转写结果。录音或上传音频后点击「转写」。',
      'transcript.progressTitle': '正在转写音频',
      'transcript.progressSub': '首次加载模型可能需要 20–60 秒，请保持页面打开。',
      'phase.prepare': '准备音频',
      'phase.load': '加载模型',
      'phase.decode': '识别语音',
      'phase.finalize': '整理文本',
      'phase.save': '写入结果',
      'transcript.progressClock': '已识别 {done} / {total} · 已用 {elapsed}',
      'transcript.progressElapsed': '已用 {elapsed}',
      'toast.transcribeTimeout': '转写超时，请重试或改用更短的录音。',
      'meta.local': '音频仅在本机处理',
      'meta.lang': '{lang} · 置信 {conf}',
      'meta.langUnknown': '语种待识别',
      'meta.segments': '{n} 段 · {dur}',
      'segment.play': '播放此段',
      'segment.confirm': '确认无误',
      'segment.confirmed': '已确认',
      'segment.low': '低置信',
      'segment.edited': '已人工修改',
      'conf.global': '整段语言概率',
      'conf.manual': '人工',
      'corrections.title': '术语纠正记录',
      'corrections.count': '{n} 处',
      'corrections.before': '原文',
      'corrections.after': '改为',
      'corrections.rule': '规则',
      'corrections.segment': '分段',
      'report.title': '报告初稿',
      'report.empty': '尚未生成初稿。完成转写后点击「生成初稿」。',
      'report.required': '必填',
      'report.sign': '签署报告',
      'report.export': '导出 PDF / JSON',
      'report.accept': '采纳',
      'report.save': '保存编辑',
      'report.reject': '拒绝',
      'report.applied': '已采纳',
      'report.edited': '已编辑',
      'report.rejected': '已拒绝',
      'report.source': '来源',
      'report.noSource': '无来源引用',
      'report.suggest': '建议内容',
      'report.options': '可参考',
      'report.adopt': '采纳建议',
      'report.pending': '待处理',
      'report.notMentioned': '未提及',
      'report.unknown': '未分类',
      'gate.needGenerate': '生成初稿后才能签署。',
      'gate.ready': '所有必填项已处理，可以签署。',
      'gate.missingBoth': '还有 {low}未确认、{missing} 个必填字段未处理。',
      'gate.missingLow': '还有 {low}未确认。',
      'gate.missingField': '还有 {missing} 个必填字段未处理。',
      'gate.signed': '报告已签署，内容已锁定，可导出。',
      'dialog.title': '签署前确认',
      'dialog.sub': '签署后内容锁定，只能查看或导出。',
      'dialog.close': '关闭',
      'ext.title': '安装扩展模型',
      'ext.sub': '该模型以扩展包形式提供，下载后按下列步骤安装；装好即可离线使用。',
      'ext.size': '包大小',
      'ext.sha': 'SHA-256',
      'ext.steps': '安装步骤',
      'ext.open': '打开下载地址',
      'ext.close': '关闭',
      'ext.uninstalled': ' · 未安装',
      'ext.installed': ' · 扩展',
      'ext.copy': '复制链接',
      'ext.copied': '已复制',
      'dialog.consent': '我已逐项核对报告内容，确认签署。此操作会写入审计记录。',
      'dialog.cancel': '取消',
      'dialog.sign': '确认签署',
      'dialog.checkLow': '全部低置信片段已确认',
      'dialog.checkLowPending': '还有 {n} 个低置信片段未确认',
      'dialog.checkRequired': '必填字段已全部填写',
      'dialog.checkRequiredPending': '还有 {n} 个必填字段未填写',
      'dialog.checkTrace': '报告字段可回溯到转写片段',
      'export.title': '导出预览',
      'export.sub': '核对版式与字段后再下载。',
      'export.formatAria': '导出格式',
      'export.pdf': 'PDF 版式',
      'export.json': 'JSON 数据',
      'export.real': '本地文件',
      'export.download': '下载 PDF',
      'export.downloadJson': '下载 JSON',
      'toast.created': '已新建会话。',
      'toast.recording': '录音中…（演示请使用合成语料）',
      'toast.recorded': '录音已上传：{dur}',
      'toast.uploaded': '音频已保存：{dur}',
      'toast.transcribing': '转写中（首次加载模型可能较慢）…',
      'toast.transcribed': '转写完成：{n} 段，{low} 段低置信',
      'toast.corrected': '术语纠正完成：{n} 处替换',
      'toast.undone': '已撤销纠正：还原 {n} 段',
      'toast.extracted': '抽取完成：{n} 条要素',
      'toast.generated': '初稿已生成：{n} 个字段',
      'toast.saved': '片段已保存。',
      'toast.accepted': '字段已采纳。',
      'toast.edited': '字段已保存编辑。',
      'toast.rejected': '字段已拒绝。',
      'toast.signed': '报告已签署。',
      'toast.blocked': '签署被阻止：{why}',
      'toast.exported': '已导出 {fmt}。',
      'toast.deleted': '会话已删除。',
      'toast.noSession': '请先新建或选择会话。',
      'toast.noAudio': '请先录音或上传音频。',
      'toast.noReport': '请先生成初稿。',
      'toast.confirmDelete': '删除会话会同时删除音频、报告与导出记录，确定？',
      'toast.error': '操作失败：{msg}',
      'toast.loadFailed': '加载失败：{msg}',
      'health.checking': '检查中',
      'health.ok': '本地服务正常',
      'health.fail': '本地服务异常',
      'health.down': '无法连接集成层',
      'health.title': '本地服务',
      'health.asr': '语音识别',
      'health.report': '报告引擎',
      'health.integration': '集成层',
      'health.tooltip': '查看本地服务状态',
      'health.aria': '服务状态',
      'help.aria': '查看帮助',
      'help.title': '帮助说明',
      'help.shortcuts': '快捷键：T 切换主题，/ 聚焦搜索，Esc 关闭弹层。',
      'help.asrTitle': '语音识别与转录实现',
      'help.asrLead': '默认使用本机 faster-whisper small（CPU int8，音频不出本机）。工具条「模型」下拉内置 small，另有 Qwen3-ASR、Whisper medium、Whisper large-v3 扩展；未安装项会弹出下载地址与步骤，不会联网下载。CapsWriter 高精度档在病理术语上更准，但耗时更长。',
      'help.table.dim': '维度',
      'help.table.default': '默认：faster-whisper',
      'help.table.optional': '可选：CapsWriter-Offline',
      'help.table.os': '系统',
      'help.table.os.default': 'Windows / macOS / Linux，需 ffmpeg',
      'help.table.os.optional': '仅保证 Windows 10/11 64 位，需 VC++ 运行库与 ffmpeg',
      'help.table.cpu': 'CPU',
      'help.table.cpu.default': '4 核起，无需独显',
      'help.table.cpu.optional': '无独显可用 SenseVoice-Small / Fun-ASR-Nano',
      'help.table.ram': '内存',
      'help.table.ram.default': '约 2 GB 量级',
      'help.table.ram.optional': '官方未给统一门槛，模型包 0.5–1.5 GB',
      'help.table.gpu': '显卡',
      'help.table.gpu.default': '非必需；CUDA 可选，约 1.5–2 GB 显存',
      'help.table.gpu.optional': '可选 DirectML / Vulkan；显存 <1 GB 至 1–2 GB',
      'help.asrNote': '两条路径都在本机处理音频；切换前请在同一批语料上比较准确率与耗时。项目内尚未接入 CapsWriter 适配层。',
      'help.benchTitle': '实测数据（2026-11）',
      'help.benchEnv': '测试机：Intel Core Ultra 7 270K Plus（24 核）/ 32 GB / RTX 5070 Ti 16 GB；3 条合成语料 + 6 种扰动；错误率越低越好。',
      'help.bench.model': '模型',
      'help.bench.err': '平均错误率',
      'help.bench.cpu': 'CPU RTF',
      'help.bench.gpu': 'GPU RTF',
      'help.bench.mem': '内存 / 显存',
      'help.pickTitle': '选型建议',
      'help.pick1': '有 8 GB 显卡：综合选 medium；病理长术语选 large-v3 或 Qwen3-ASR。',
      'help.pick2': '无显卡：8 核选 small；4 核或内存 ≤8 GB 选 SenseVoice。',
      'help.pick3': '噪声或长音频：medium 最稳；large-v3 在噪声下退化，需前置降噪。',
      'help.benchNote': '完整数据表与局限见 outputs/ui-integration/asr-benchmark/REPORT.md；合成语料，结论为方向性。',
      'more.aria': '更多操作',
      'more.title': '更多操作',
      'more.delete': '删除会话',
      'field.empty': '（空）'
    },
    'zh-Hant': {
      'session.new': '新增會話',
      'search.placeholder': '搜尋病人或會話編號',
      'search.aria': '搜尋會話',
      'rail.aria': '會話列表',
      'rail.empty': '暫無會話',
      'rail.today': '今天',
      'rail.earlier': '更早',
      'rail.count': '{n} 例',
      'pref.theme': '介面主題',
      'pref.themeAria': '介面主題',
      'pref.language': '介面語言',
      'pref.languageAria': '介面語言',
      'theme.light': '淺色',
      'theme.dark': '深色',
      'theme.system': '系統',
      'theme.switch': '切換主題',
      'language.zh': '简体中文',
      'language.zhHant': '繁體中文',
      'language.en': 'English',
      'language.switch': '切換語言',
      'zoom.hint': 'Ctrl+滾輪以縮放介面',
      'avatar.user': '林',
      'user.name': '林醫生',
      'user.role': '內科',
      'patient.empty': '未選擇會話',
      'status.created': '已建會話',
      'status.recorded': '已錄音',
      'status.transcribed': '已轉寫',
      'status.corrected': '術語已糾正',
      'status.extracted': '已抽取',
      'status.generated': '待審核',
      'status.signed': '已簽署',
      'status.exported': '已匯出',
      'stepper.aria': '記錄流程',
      'step.record': '錄音',
      'step.transcribe': '轉寫',
      'step.review': '校對',
      'step.extract': '抽取',
      'step.approve': '審核',
      'step.sign': '簽署',
      'empty.title': '還沒有會話',
      'empty.sub': '新增會話後，按「錄音 → 轉寫 → 術語糾正 → 抽取 → 生成初稿 → 審核 → 簽署 → 匯出」完成一次病歷記錄。演示只使用合成語料。',
      'empty.cta': '新增會話',
      'review.title': '轉寫校對',
      'review.sub': '核對低信心度內容並修正文字。報告欄位隨審核更新；必填項和低信心度片段未處理前不可簽署。',
      'filters.aria': '片段篩選',
      'filter.all': '全部',
      'filter.low': '低信心度',
      'filter.confirmed': '已確認',
      'soap.s': '主觀',
      'soap.o': '客觀',
      'soap.a': '評估',
      'soap.p': '計劃',
      'audio.aria': '音頻播放器',
      'audio.play': '播放音頻',
      'audio.pause': '暫停音頻',
      'audio.speed': '播放速度',
      'audio.record': '錄音',
      'audio.stop': '停止',
      'audio.upload': '上傳音頻',
      'action.template': '模板',
      'action.model': '模型',
      'action.modelAria': '語音識別模型',
      'action.language': '語言',
      'action.languageAria': '識別語言',
      'asr.lang.auto': '自動識別',
      'asr.lang.zh': '中文',
      'asr.lang.yue': '粵語',
      'asr.lang.en': '英語',
      'asr.lang.ja': '日語',
      'asr.lang.ko': '韓語',
      'model.localSuffix': ' · 本地',
      'model.remoteSuffix': ' · 未安裝',
      'model.switching': '正在切換模型：{name}（首次使用需載入模型，可能耗時數十秒）…',
      'model.switched': '已切換到 {name}（{s}s）',
      'action.transcribe': '轉寫',
      'action.transcribing': '轉寫中…',
      'action.correct': '術語糾正',
      'action.undo': '撤銷糾正',
      'action.extract': '抽取 S/O/A/P',
      'action.generate': '生成初稿',
      'transcript.title': '轉寫原文',
      'transcript.empty': '尚無轉寫結果。錄音或上傳音頻後點擊「轉寫」。',
      'transcript.progressTitle': '正在轉寫音頻',
      'transcript.progressSub': '首次加載模型可能需要 20–60 秒，請保持頁面打開。',
      'phase.prepare': '準備音頻',
      'phase.load': '加載模型',
      'phase.decode': '識別語音',
      'phase.finalize': '整理文本',
      'phase.save': '寫入結果',
      'transcript.progressClock': '已識別 {done} / {total} · 已用 {elapsed}',
      'transcript.progressElapsed': '已用 {elapsed}',
      'toast.transcribeTimeout': '轉寫超時，請重試或改用更短的錄音。',
      'meta.local': '音頻僅在本機處理',
      'meta.lang': '{lang} · 信心度 {conf}',
      'meta.langUnknown': '語種待識別',
      'meta.segments': '{n} 段 · {dur}',
      'segment.play': '播放此段',
      'segment.confirm': '確認無誤',
      'segment.confirmed': '已確認',
      'segment.low': '低信心度',
      'segment.edited': '已人工修改',
      'conf.global': '整段語言機率',
      'conf.manual': '人工',
      'corrections.title': '術語糾正記錄',
      'corrections.count': '{n} 處',
      'corrections.before': '原文',
      'corrections.after': '改為',
      'corrections.rule': '規則',
      'corrections.segment': '分段',
      'report.title': '報告初稿',
      'report.empty': '尚未生成初稿。完成轉寫後點擊「生成初稿」。',
      'report.required': '必填',
      'report.sign': '簽署報告',
      'report.export': '匯出 PDF / JSON',
      'report.accept': '採用',
      'report.save': '儲存編輯',
      'report.reject': '拒絕',
      'report.applied': '已採用',
      'report.edited': '已編輯',
      'report.rejected': '已拒絕',
      'report.source': '來源',
      'report.noSource': '無來源引用',
      'report.suggest': '建議內容',
      'report.options': '可參考',
      'report.adopt': '採用建議',
      'report.pending': '待處理',
      'report.notMentioned': '未提及',
      'report.unknown': '未分類',
      'gate.needGenerate': '生成初稿後才能簽署。',
      'gate.ready': '所有必填項已處理，可以簽署。',
      'gate.missingBoth': '還有 {low}未確認、{missing} 個必填欄位未處理。',
      'gate.missingLow': '還有 {low}未確認。',
      'gate.missingField': '還有 {missing} 個必填欄位未處理。',
      'gate.signed': '報告已簽署，內容已鎖定，可匯出。',
      'dialog.title': '簽署前確認',
      'dialog.sub': '簽署後內容鎖定，只能檢視或匯出。',
      'dialog.close': '關閉',
      'ext.title': '安裝擴展模型',
      'ext.sub': '該模型以擴展包形式提供，下載後按下列步驟安裝；裝好即可離線使用。',
      'ext.size': '包大小',
      'ext.sha': 'SHA-256',
      'ext.steps': '安裝步驟',
      'ext.open': '開啟下載位址',
      'ext.close': '關閉',
      'ext.uninstalled': ' · 未安裝',
      'ext.installed': ' · 擴展',
      'ext.copy': '複製連結',
      'ext.copied': '已複製',
      'dialog.consent': '我已逐項核對報告內容，確認簽署。此操作會寫入稽核記錄。',
      'dialog.cancel': '取消',
      'dialog.sign': '確認簽署',
      'dialog.checkLow': '全部低信心度片段已確認',
      'dialog.checkLowPending': '還有 {n} 個低信心度片段未確認',
      'dialog.checkRequired': '必填欄位已全部填寫',
      'dialog.checkRequiredPending': '還有 {n} 個必填欄位未填寫',
      'dialog.checkTrace': '報告欄位可回溯到轉寫片段',
      'export.title': '匯出預覽',
      'export.sub': '核對版面與欄位後再下載。',
      'export.formatAria': '匯出格式',
      'export.pdf': 'PDF 版面',
      'export.json': 'JSON 資料',
      'export.real': '本機檔案',
      'export.download': '下載 PDF',
      'export.downloadJson': '下載 JSON',
      'toast.created': '已新增會話。',
      'toast.recording': '錄音中…（演示請使用合成語料）',
      'toast.recorded': '錄音已上傳：{dur}',
      'toast.uploaded': '音頻已儲存：{dur}',
      'toast.transcribing': '轉寫中（首次載入模型可能較慢）…',
      'toast.transcribed': '轉寫完成：{n} 段，{low} 段低信心度',
      'toast.corrected': '術語糾正完成：{n} 處替換',
      'toast.undone': '已撤銷糾正：還原 {n} 段',
      'toast.extracted': '抽取完成：{n} 條要素',
      'toast.generated': '初稿已生成：{n} 個欄位',
      'toast.saved': '片段已儲存。',
      'toast.accepted': '欄位已採用。',
      'toast.edited': '欄位已儲存編輯。',
      'toast.rejected': '欄位已拒絕。',
      'toast.signed': '報告已簽署。',
      'toast.blocked': '簽署被阻止：{why}',
      'toast.exported': '已匯出 {fmt}。',
      'toast.deleted': '會話已刪除。',
      'toast.noSession': '請先新增或選擇會話。',
      'toast.noAudio': '請先錄音或上傳音頻。',
      'toast.noReport': '請先生成初稿。',
      'toast.confirmDelete': '刪除會話會同時刪除音頻、報告與匯出記錄，確定？',
      'toast.error': '操作失敗：{msg}',
      'toast.loadFailed': '載入失敗：{msg}',
      'health.checking': '檢查中',
      'health.ok': '本機服務正常',
      'health.fail': '本機服務異常',
      'health.down': '無法連線整合層',
      'health.title': '本機服務',
      'health.asr': '語音識別',
      'health.report': '報告引擎',
      'health.integration': '整合層',
      'health.tooltip': '檢視本機服務狀態',
      'health.aria': '服務狀態',
      'help.aria': '檢視說明',
      'help.title': '說明',
      'help.shortcuts': '快捷鍵：T 切換主題，/ 聚焦搜尋，Esc 關閉彈層。',
      'help.asrTitle': '語音辨識與轉錄實作',
      'help.asrLead': '預設使用本機 faster-whisper small（CPU int8，音訊不出本機）。工具列「模型」下拉內置 small，另有 Qwen3-ASR、Whisper medium、Whisper large-v3 擴展；未安裝項會彈出下載地址與步驟，不會聯網下載。CapsWriter 高精度檔在病理術語上更準，但耗時更長。',
      'help.table.dim': '面向',
      'help.table.default': '預設：faster-whisper',
      'help.table.optional': '可選：CapsWriter-Offline',
      'help.table.os': '系統',
      'help.table.os.default': 'Windows / macOS / Linux，需 ffmpeg',
      'help.table.os.optional': '僅保證 Windows 10/11 64 位元，需 VC++ 執行階段與 ffmpeg',
      'help.table.cpu': 'CPU',
      'help.table.cpu.default': '4 核起，無需獨立顯卡',
      'help.table.cpu.optional': '無獨立顯卡可用 SenseVoice-Small / Fun-ASR-Nano',
      'help.table.ram': '記憶體',
      'help.table.ram.default': '約 2 GB 量級',
      'help.table.ram.optional': '官方未給統一門檻，模型包 0.5–1.5 GB',
      'help.table.gpu': '顯卡',
      'help.table.gpu.default': '非必需；CUDA 可選，約 1.5–2 GB 顯存',
      'help.table.gpu.optional': '可選 DirectML / Vulkan；顯存 <1 GB 至 1–2 GB',
      'help.asrNote': '兩條路徑都在本機處理音訊；切換前請在同一批語料上比較準確度與耗時。專案內尚未接入 CapsWriter 適配層。',
      'help.benchTitle': '實測資料（2026-11）',
      'help.benchEnv': '測試機：Intel Core Ultra 7 270K Plus（24 核）/ 32 GB / RTX 5070 Ti 16 GB；3 條合成語料 + 6 種擾動；錯誤率越低越好。',
      'help.bench.model': '模型',
      'help.bench.err': '平均錯誤率',
      'help.bench.cpu': 'CPU RTF',
      'help.bench.gpu': 'GPU RTF',
      'help.bench.mem': '記憶體 / 顯存',
      'help.pickTitle': '選型建議',
      'help.pick1': '有 8 GB 顯卡：綜合選 medium；病理長術語選 large-v3 或 Qwen3-ASR。',
      'help.pick2': '無顯卡：8 核選 small；4 核或記憶體 ≤8 GB 選 SenseVoice。',
      'help.pick3': '雜訊或長音訊：medium 最穩；large-v3 在雜訊下退化，需前置降噪。',
      'help.benchNote': '完整資料表與限制見 outputs/ui-integration/asr-benchmark/REPORT.md；合成語料，結論為方向性。',
      'more.aria': '更多操作',
      'more.title': '更多操作',
      'more.delete': '刪除會話',
      'field.empty': '（空）'
    },
    en: {
      'session.new': 'New session',
      'search.placeholder': 'Search patient or session',
      'search.aria': 'Search sessions',
      'rail.aria': 'Sessions',
      'rail.empty': 'No sessions yet',
      'rail.today': 'Today',
      'rail.earlier': 'Earlier',
      'rail.count': '{n} cases',
      'pref.theme': 'Theme',
      'pref.themeAria': 'Theme',
      'pref.language': 'Language',
      'pref.languageAria': 'Language',
      'theme.light': 'Light',
      'theme.dark': 'Dark',
      'theme.system': 'System',
      'theme.switch': 'Switch theme',
      'language.zh': '简体中文',
      'language.zhHant': '繁體中文',
      'language.en': 'English',
      'language.switch': 'Switch language',
      'zoom.hint': 'Ctrl + scroll to zoom',
      'avatar.user': 'L',
      'user.name': 'Dr. Lam',
      'user.role': 'Internal medicine',
      'patient.empty': 'No session selected',
      'status.created': 'Created',
      'status.recorded': 'Audio ready',
      'status.transcribed': 'Transcribed',
      'status.corrected': 'Terms corrected',
      'status.extracted': 'Extracted',
      'status.generated': 'Awaiting review',
      'status.signed': 'Signed',
      'status.exported': 'Exported',
      'stepper.aria': 'Record workflow',
      'step.record': 'Record',
      'step.transcribe': 'Transcribe',
      'step.review': 'Review',
      'step.extract': 'Extract',
      'step.approve': 'Approve',
      'step.sign': 'Sign',
      'empty.title': 'No sessions yet',
      'empty.sub': 'Create a session, then record, transcribe, correct terms, extract, draft, review, sign and export. Synthetic data only.',
      'empty.cta': 'New session',
      'review.title': 'Transcript review',
      'review.sub': 'Check low-confidence content and fix the text. Report fields update as you review; signing stays locked until required fields and low-confidence segments are handled.',
      'filters.aria': 'Segment filters',
      'filter.all': 'All',
      'filter.low': 'Low confidence',
      'filter.confirmed': 'Confirmed',
      'soap.s': 'Subjective',
      'soap.o': 'Objective',
      'soap.a': 'Assessment',
      'soap.p': 'Plan',
      'audio.aria': 'Audio player',
      'audio.play': 'Play audio',
      'audio.pause': 'Pause audio',
      'audio.speed': 'Playback speed',
      'audio.record': 'Record',
      'audio.stop': 'Stop',
      'audio.upload': 'Upload audio',
      'action.template': 'Template',
      'action.model': 'Model',
      'action.modelAria': 'Speech recognition model',
      'action.language': 'Language',
      'action.languageAria': 'Recognition language',
      'asr.lang.auto': 'Auto detect',
      'asr.lang.zh': 'Chinese',
      'asr.lang.yue': 'Cantonese',
      'asr.lang.en': 'English',
      'asr.lang.ja': 'Japanese',
      'asr.lang.ko': 'Korean',
      'model.localSuffix': ' · local',
      'model.remoteSuffix': ' · not installed',
      'model.switching': 'Switching model: {name} (first use loads the local model, may take up to a minute)…',
      'model.switched': 'Switched to {name} ({s}s)',
      'action.transcribe': 'Transcribe',
      'action.transcribing': 'Transcribing…',
      'action.correct': 'Correct terms',
      'action.undo': 'Undo corrections',
      'action.extract': 'Extract S/O/A/P',
      'action.generate': 'Generate draft',
      'transcript.title': 'Transcript',
      'transcript.empty': 'No transcript yet. Record or upload audio, then choose Transcribe.',
      'transcript.progressTitle': 'Transcribing audio',
      'transcript.progressSub': 'The first run may take 20–60 seconds while the model loads. Keep this page open.',
      'phase.prepare': 'Preparing audio',
      'phase.load': 'Loading model',
      'phase.decode': 'Recognising speech',
      'phase.finalize': 'Assembling text',
      'phase.save': 'Saving result',
      'transcript.progressClock': '{done} / {total} recognised · {elapsed} elapsed',
      'transcript.progressElapsed': '{elapsed} elapsed',
      'toast.transcribeTimeout': 'Transcription timed out. Try again or use a shorter recording.',
      'meta.local': 'Audio stays on this device',
      'meta.lang': '{lang} · confidence {conf}',
      'meta.langUnknown': 'Language pending',
      'meta.segments': '{n} segments · {dur}',
      'segment.play': 'Play segment',
      'segment.confirm': 'Confirm',
      'segment.confirmed': 'Confirmed',
      'segment.low': 'Low confidence',
      'segment.edited': 'Edited',
      'conf.global': 'Whole-audio language probability',
      'conf.manual': 'Manual',
      'corrections.title': 'Term corrections',
      'corrections.count': '{n}',
      'corrections.before': 'Before',
      'corrections.after': 'After',
      'corrections.rule': 'Rule',
      'corrections.segment': 'Segment',
      'report.title': 'Report draft',
      'report.empty': 'No draft yet. Transcribe first, then choose Generate draft.',
      'report.required': 'Required',
      'report.sign': 'Sign report',
      'report.export': 'Export PDF / JSON',
      'report.accept': 'Accept',
      'report.save': 'Save edit',
      'report.reject': 'Reject',
      'report.applied': 'Accepted',
      'report.edited': 'Edited',
      'report.rejected': 'Rejected',
      'report.source': 'Source',
      'report.noSource': 'No source reference',
      'report.suggest': 'Suggestion',
      'report.options': 'Candidates',
      'report.adopt': 'Adopt suggestion',
      'report.pending': 'Pending',
      'report.notMentioned': 'Not mentioned',
      'report.unknown': 'Unclassified',
      'gate.needGenerate': 'Generate a draft before signing.',
      'gate.ready': 'All required items are complete. Ready to sign.',
      'gate.missingBoth': '{low} unconfirmed; {missing} required field(s) pending.',
      'gate.missingLow': '{low} unconfirmed.',
      'gate.missingField': '{missing} required field(s) pending.',
      'gate.signed': 'Report signed. Content is locked; export is available.',
      'dialog.title': 'Confirm signing',
      'dialog.sub': 'After signing, content is locked and can only be viewed or exported.',
      'dialog.close': 'Close',
      'ext.title': 'Install extension model',
      'ext.sub': 'This model ships as an extension pack. Download it, then follow the steps below; it runs fully offline once installed.',
      'ext.size': 'Pack size',
      'ext.sha': 'SHA-256',
      'ext.steps': 'Install steps',
      'ext.open': 'Open download page',
      'ext.close': 'Close',
      'ext.uninstalled': ' · not installed',
      'ext.installed': ' · extension',
      'ext.copy': 'Copy link',
      'ext.copied': 'Copied',
      'dialog.consent': 'I have reviewed every item and confirm signing. This action is written to the audit log.',
      'dialog.cancel': 'Cancel',
      'dialog.sign': 'Sign',
      'dialog.checkLow': 'All low-confidence segments confirmed',
      'dialog.checkLowPending': '{n} low-confidence segment(s) still unconfirmed',
      'dialog.checkRequired': 'All required fields completed',
      'dialog.checkRequiredPending': '{n} required field(s) still pending',
      'dialog.checkTrace': 'Report fields trace back to transcript segments',
      'export.title': 'Export preview',
      'export.sub': 'Check the layout and fields before downloading.',
      'export.formatAria': 'Export format',
      'export.pdf': 'PDF layout',
      'export.json': 'JSON data',
      'export.real': 'Local file',
      'export.download': 'Download PDF',
      'export.downloadJson': 'Download JSON',
      'toast.created': 'Session created.',
      'toast.recording': 'Recording… (synthetic data only)',
      'toast.recorded': 'Recording uploaded: {dur}',
      'toast.uploaded': 'Audio saved: {dur}',
      'toast.transcribing': 'Transcribing (first run may load the model)…',
      'toast.transcribed': 'Transcript ready: {n} segments, {low} low confidence',
      'toast.corrected': 'Term correction done: {n} replacements',
      'toast.undone': 'Corrections undone: {n} segments restored',
      'toast.extracted': 'Extracted {n} elements',
      'toast.generated': 'Draft generated: {n} fields',
      'toast.saved': 'Segment saved.',
      'toast.accepted': 'Field accepted.',
      'toast.edited': 'Field edit saved.',
      'toast.rejected': 'Field rejected.',
      'toast.signed': 'Report signed.',
      'toast.blocked': 'Signing blocked: {why}',
      'toast.exported': 'Exported {fmt}.',
      'toast.deleted': 'Session deleted.',
      'toast.noSession': 'Create or select a session first.',
      'toast.noAudio': 'Record or upload audio first.',
      'toast.noReport': 'Generate a draft first.',
      'toast.confirmDelete': 'Deleting this session also removes its audio, report and export records. Continue?',
      'toast.error': 'Action failed: {msg}',
      'toast.loadFailed': 'Load failed: {msg}',
      'health.checking': 'Checking',
      'health.ok': 'Local services OK',
      'health.fail': 'Local services degraded',
      'health.down': 'Integration layer unreachable',
      'health.title': 'Local services',
      'health.asr': 'Speech recognition',
      'health.report': 'Report engine',
      'health.integration': 'Integration',
      'health.tooltip': 'View local service status',
      'health.aria': 'Service status',
      'help.aria': 'View help',
      'help.title': 'Help',
      'help.shortcuts': 'Shortcuts: T switches theme, / focuses search, Esc closes dialogs.',
      'help.asrTitle': 'Speech recognition & transcription',
      'help.asrLead': 'The default is local faster-whisper small (CPU int8; audio never leaves this machine). The Model dropdown ships with small, plus optional Qwen3-ASR, Whisper medium and Whisper large-v3 packs; uninstalled entries show a download link and steps, and nothing is fetched online. CapsWriter\'s high-accuracy tier leads on pathology terms but is slower.',
      'help.table.dim': 'Aspect',
      'help.table.default': 'Default: faster-whisper',
      'help.table.optional': 'Optional: CapsWriter-Offline',
      'help.table.os': 'OS',
      'help.table.os.default': 'Windows / macOS / Linux, with ffmpeg',
      'help.table.os.optional': 'Windows 10/11 64-bit only, needs VC++ runtime and ffmpeg',
      'help.table.cpu': 'CPU',
      'help.table.cpu.default': '4 cores, no discrete GPU',
      'help.table.cpu.optional': 'Runs without a discrete GPU (SenseVoice-Small / Fun-ASR-Nano)',
      'help.table.ram': 'Memory',
      'help.table.ram.default': 'Around 2 GB',
      'help.table.ram.optional': 'No single official minimum; model packs 0.5–1.5 GB',
      'help.table.gpu': 'GPU',
      'help.table.gpu.default': 'Not required; CUDA optional, ~1.5–2 GB VRAM',
      'help.table.gpu.optional': 'DirectML / Vulkan optional; <1 GB to 1–2 GB VRAM',
      'help.asrNote': 'Both paths process audio locally. Compare accuracy and latency on the same corpus before switching. The CapsWriter adapter is not wired into this project yet.',
      'help.benchTitle': 'Measured results (Nov 2026)',
      'help.benchEnv': 'Test box: Intel Core Ultra 7 270K Plus (24 cores) / 32 GB / RTX 5070 Ti 16 GB; 3 synthetic clips + 6 perturbations; lower error is better.',
      'help.bench.model': 'Model',
      'help.bench.err': 'Avg error',
      'help.bench.cpu': 'CPU RTF',
      'help.bench.gpu': 'GPU RTF',
      'help.bench.mem': 'RAM / VRAM',
      'help.pickTitle': 'How to choose',
      'help.pick1': '8 GB+ GPU: medium for general use; large-v3 or Qwen3-ASR for pathology terms.',
      'help.pick2': 'No GPU: small on 8 cores; SenseVoice on 4 cores or ≤8 GB RAM.',
      'help.pick3': 'Noise or long audio: medium is the most stable; large-v3 degrades, add denoising first.',
      'help.benchNote': 'Full tables and caveats: outputs/ui-integration/asr-benchmark/REPORT.md. Synthetic corpus; treat results as directional.',
      'more.aria': 'More actions',
      'more.title': 'More actions',
      'more.delete': 'Delete session',
      'field.empty': '(empty)'
    }
  };

  function t(key, vars) {
    var table = I18N[state.lang] || I18N.zh;
    var value = table[key] || I18N.zh[key] || key;
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        value = value.replace(new RegExp('\\{' + name + '\\}', 'g'), vars[name]);
      });
    }
    return value;
  }

  /* ----------------------------------------------------------------- utils */
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function fmtClock(ms) {
    var total = Math.max(0, Math.round((ms || 0) / 1000));
    var m = Math.floor(total / 60);
    var s = total % 60;
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  function rank(status) {
    return RANK[status] == null ? 0 : RANK[status];
  }

  var toastEl = $('toast');
  var toastTimer = null;
  function showToast(message) {
    toastEl.textContent = message;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 3200);
  }

  function fail(error) {
    var message = error && error.message ? error.message : String(error);
    showToast(t('toast.error', { msg: message }));
  }

  async function api(path, options) {
    var response = await fetch(path, options || {});
    var type = response.headers.get('content-type') || '';
    var body;
    if (type.indexOf('json') >= 0) {
      body = await response.json().catch(function () { return null; });
    } else {
      body = await response.text();
    }
    if (!response.ok) {
      var detail = body && body.detail != null ? body.detail : body;
      if (typeof detail !== 'string') detail = JSON.stringify(detail);
      throw new Error(detail || ('HTTP ' + response.status));
    }
    return body;
  }

  function jsonOptions(method, payload) {
    return {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload == null ? {} : payload)
    };
  }

  /* ---------------------------------------------------------------- theme */
  function applyTheme(mode, persist) {
    if (persist !== false) {
      try { localStorage.setItem(THEME_KEY, mode); } catch (e) {}
    }
    var resolved = mode === 'system' ? (media.matches ? 'dark' : 'light') : mode;
    root.dataset.theme = resolved;
    root.dataset.themeMode = mode;
    document.querySelectorAll('[data-theme-set]').forEach(function (btn) {
      btn.setAttribute('aria-pressed', String(btn.dataset.themeSet === mode));
    });
    var mobileTheme = $('mobile-theme');
    if (mobileTheme) {
      mobileTheme.setAttribute('aria-label', t('theme.switch'));
      mobileTheme.setAttribute('title', t('theme.switch'));
    }
  }

  function cycleTheme() {
    var order = ['light', 'dark', 'system'];
    var current = root.dataset.themeMode || 'system';
    applyTheme(order[(order.indexOf(current) + 1) % order.length], true);
  }

  /* --------------------------------------------------------------- session */
  function currentReport() {
    return state.detail ? state.detail.report : null;
  }

  function isSigned() {
    return Boolean(state.detail && rank(state.detail.session.status) >= rank('signed'));
  }

  function lowSegments() {
    return (state.detail && state.detail.segments ? state.detail.segments : [])
      .filter(function (seg) { return seg.low_confidence; });
  }

  function unconfirmedLow() {
    return lowSegments().filter(function (seg) { return !seg.confirmed; });
  }

  function missingRequired() {
    var report = currentReport();
    return report && report.missing_required ? report.missing_required.slice() : [];
  }

  function lowPhrase(n) {
    if (state.lang === 'en') return n + ' low-confidence ' + (n === 1 ? 'segment' : 'segments');
    if (state.lang === 'zh-Hant') return n + ' 個低信心度片段';
    return n + ' 個低置信片段';
  }

  function displayValue(field, review) {
    if (review && review.action === 'reject') return '';
    if (field.final_value) return field.final_value;
    if (!review && (field.flags || []).indexOf('missing') >= 0) return '';
    return field.ai_value || '';
  }

  function reviewsByKey(report) {
    var map = {};
    if (report && report.reviews) {
      report.reviews.forEach(function (review) { map[review.field_key] = review; });
    }
    return map;
  }

  function sectionOf(field) {
    var section = String(field.section || '').toUpperCase();
    if (['S', 'O', 'A', 'P'].indexOf(section) >= 0) return section;
    var key = String(field.key || '').toUpperCase();
    if (key.indexOf('SUBJ') === 0 || key.indexOf('CHIEF') === 0 || key.indexOf('HISTORY') === 0) return 'S';
    if (key.indexOf('OBJ') === 0 || key.indexOf('PHYSICAL') === 0 || key.indexOf('VITAL') === 0 ||
        key.indexOf('EXAM') === 0 || key.indexOf('INVEST') === 0) return 'O';
    if (key.indexOf('ASSESS') === 0 || key.indexOf('DIAGNOS') === 0) return 'A';
    if (key.indexOf('PLAN') === 0 || key.indexOf('TREAT') === 0 || key.indexOf('FOLLOW') === 0) return 'P';
    return 'U';
  }

  function soapForSegment(segmentId) {
    var report = currentReport();
    if (!report || !report.fields) return null;
    for (var i = 0; i < report.fields.length; i += 1) {
      var field = report.fields[i];
      var sources = field.sources || [];
      for (var j = 0; j < sources.length; j += 1) {
        if (sources[j].segment_id === segmentId) return sectionOf(field);
      }
    }
    return null;
  }

  function sectionLabel(section) {
    var map = { S: 'soap.s', O: 'soap.o', A: 'soap.a', P: 'soap.p' };
    return map[section] ? t(map[section]) : t('report.unknown');
  }

  /* ------------------------------------------------------------ rendering */
  function renderSessions() {
    var box = $('session-list');
    var query = ($('session-search').value || '').trim().toLowerCase();
    var items = state.sessions.filter(function (session) {
      if (!query) return true;
      return ((session.title || '') + ' ' + session.id).toLowerCase().indexOf(query) >= 0;
    });

    if (!items.length) {
      box.innerHTML = '<div class="rail-empty">' +
        (query ? esc(t('rail.empty')) : esc(t('rail.empty'))) + '</div>';
      return;
    }

    var groups = { today: [], earlier: [] };
    var now = new Date();
    items.forEach(function (session) {
      var day = new Date(session.created_at || session.updated_at || Date.now());
      var sameDay = day.getFullYear() === now.getFullYear() &&
        day.getMonth() === now.getMonth() && day.getDate() === now.getDate();
      (sameDay ? groups.today : groups.earlier).push(session);
    });

    var html = '';
    [['today', 'rail.today'], ['earlier', 'rail.earlier']].forEach(function (pair) {
      var list = groups[pair[0]];
      if (!list.length) return;
      html += '<div class="rail-label"><span>' + esc(t(pair[1])) + '</span><span>' +
        esc(t('rail.count', { n: list.length })) + '</span></div>';
      list.forEach(function (session) {
        var title = session.title || session.id;
        var status = t('status.' + (session.status || 'created'));
        var day = new Date(session.created_at || Date.now());
        var time = String(day.getHours()).padStart(2, '0') + ':' + String(day.getMinutes()).padStart(2, '0');
        var dotClass = session.report_status === 'signed' ? 'ok'
          : session.status === 'generated' ? 'warn'
          : session.status === 'created' ? 'muted' : 'live';
        html += '<button class="session' + (session.id === state.sessionId ? ' active' : '') +
          '" type="button" data-session-id="' + esc(session.id) + '"' +
          (session.id === state.sessionId ? ' aria-current="true"' : '') + '>' +
          '<span class="avatar">' + esc(String(title).slice(0, 1)) + '</span>' +
          '<span class="session-main">' +
          '<span class="session-name">' + esc(title) + '</span>' +
          '<span class="session-meta"><span class="dot ' + dotClass + '"></span><span>' +
          esc(status) + '</span></span></span>' +
          '<span class="session-time">' + esc(time) + '</span>' +
          '</button>';
      });
    });
    box.innerHTML = html;
  }

  function renderHeader() {
    var session = state.detail.session;
    $('session-title').textContent = session.title || session.id;
    var parts = [session.id, t('status.' + session.status), fmtClock(session.duration_ms)];
    if (session.language) {
      parts.push(session.language + ' (' + Math.round((session.language_probability || 0) * 100) + '%)');
    }
    $('session-meta').textContent = parts.join(' · ');
    $('status-text').textContent = t('status.' + session.status);
    $('status-chip').classList.toggle('ok', rank(session.status) >= rank('signed'));
    $('delete-session').disabled = false;
  }

  function renderStepper() {
    var session = state.detail.session;
    var current = STEP_FOR_RANK[rank(session.status)];
    var doneAll = rank(session.status) >= rank('signed');
    document.querySelectorAll('#stepper .step').forEach(function (step) {
      var index = Number(step.dataset.step);
      var done = doneAll || index < current;
      var isCurrent = !doneAll && index === current;
      step.classList.toggle('done', done);
      step.classList.toggle('current', isCurrent);
      if (isCurrent) step.setAttribute('aria-current', 'step');
      else step.removeAttribute('aria-current');
      var num = step.querySelector('.step-num');
      num.innerHTML = done
        ? '<svg class="icon icon-xs"><use href="#i-check"/></svg>'
        : String(index + 1);
    });
  }

  function renderMeta() {
    var session = state.detail.session;
    var lang = session.language || '';
    var confidence = Math.round((session.language_probability || 0) * 100);
    $('meta-lang').textContent = lang
      ? t('meta.lang', { lang: lang, conf: (confidence / 100).toFixed(2) })
      : t('meta.langUnknown');
    $('meta-speaker').textContent = t('meta.segments', {
      n: (state.detail.segments || []).length,
      dur: fmtClock(session.duration_ms)
    });
  }

  function autoGrow(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 260) + 'px';
  }

  function progressCardHtml() {
    return '<div class="tr-progress" id="tr-progress" data-phase="prepare" role="status" aria-live="polite">' +
      '<div class="tr-progress-head">' +
      '<span class="tr-spinner" aria-hidden="true"></span>' +
      '<span class="tr-progress-title">' + esc(t('transcript.progressTitle')) + '</span>' +
      '<span class="tr-progress-pct" id="tr-progress-pct">0%</span>' +
      '</div>' +
      '<div class="tr-bar"><i id="tr-progress-bar" style="width:0%"></i></div>' +
      '<p class="tr-progress-meta">' +
      '<span id="tr-progress-phase">' + esc(t('phase.prepare')) + '</span>' +
      '<span id="tr-progress-clock"></span>' +
      '</p>' +
      '<p class="tr-progress-sub">' + esc(t('transcript.progressSub')) + '</p>' +
      '</div>';
  }

  var progressTimer = null;

  function stopProgressTicker() {
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
  }

  function paintProgress(shown) {
    var bar = $('tr-progress-bar');
    var pctEl = $('tr-progress-pct');
    var pct = Math.round(Math.max(0, Math.min(1, shown)) * 100);
    if (bar) bar.style.width = pct + '%';
    if (pctEl) pctEl.textContent = pct + '%';
    var clockEl = $('tr-progress-clock');
    if (!clockEl) return;
    var job = state.transcribeJob || {};
    var total = Number(job.total) || 0;
    var done = Number(job.processed) || 0;
    var elapsed = state.transcribeStartedAt
      ? Math.max(0, (Date.now() - state.transcribeStartedAt) / 1000) : 0;
    clockEl.textContent = (total > 0 && done > 0)
      ? t('transcript.progressClock', {
        done: fmtClock(done * 1000),
        total: fmtClock(total * 1000),
        elapsed: Math.round(elapsed) + 's'
      })
      : t('transcript.progressElapsed', { elapsed: Math.round(elapsed) + 's' });
  }

  function startProgressTicker() {
    stopProgressTicker();
    state.progressShown = 0.02;
    state.progressTarget = 0.02;
    state.transcribeStartedAt = state.transcribeStartedAt || Date.now();
    progressTimer = setInterval(function () {
      var target = Number(state.progressTarget) || 0;
      var shown = Number(state.progressShown) || 0;
      if (target > shown) {
        shown += Math.max((target - shown) * 0.22, 0.004);
        if (shown > target) shown = target;
      } else if (target < 0.96) {
        // 分段回调稀疏时（长录音一次解码很久），缓慢蠕升，避免进度条完全不动
        shown = Math.min(target + 0.1, shown + 0.0007);
      }
      state.progressShown = shown;
      paintProgress(shown);
    }, 200);
    paintProgress(state.progressShown);
  }

  function updateTranscribeProgress(job) {
    state.transcribeJob = job || {};
    if (job && job.status === 'done') {
      state.progressTarget = 1;
      state.progressShown = 1;
      stopProgressTicker();
      paintProgress(1);
      return;
    }
    if (job && typeof job.progress === 'number') {
      state.progressTarget = Math.max(state.progressTarget || 0, job.progress);
    }
    var card = $('tr-progress');
    if (card) card.setAttribute('data-phase', (job && job.phase) || 'decode');
    var phaseEl = $('tr-progress-phase');
    if (phaseEl) {
      var label = t('phase.' + ((job && job.phase) || 'decode'));
      phaseEl.textContent = (label && label.indexOf('phase.') !== 0) ? label : t('phase.decode');
    }
    paintProgress(state.progressShown || 0);
  }

  function renderSegments() {
    var box = $('segments');
    var segments = state.detail.segments || [];
    var signed = isSigned();
    var corrections = state.detail.corrections || [];

    $('count-all').textContent = String(segments.length);
    $('count-low').textContent = String(unconfirmedLow().length);
    $('count-confirmed').textContent = String(
      segments.filter(function (seg) { return seg.confirmed; }).length
    );

    var low = unconfirmedLow().length;
    $('low-chip').hidden = low === 0;
    $('low-chip-text').textContent = lowPhrase(low);

    if (state.transcribing) {
      box.innerHTML = progressCardHtml();
      if (state.transcribeJob) updateTranscribeProgress(state.transcribeJob);
      return;
    }

    if (!segments.length) {
      box.innerHTML = '<p class="muted" style="padding:18px 16px">' +
        esc(t('transcript.empty')) + '</p>';
      return;
    }

    box.innerHTML = segments.map(function (seg) {
      var corrected = corrections.filter(function (item) { return item.segment_id === seg.id; });
      var soap = soapForSegment(seg.id) || 'u';
      var confidence = Math.round((seg.confidence || 0) * 100);
      var isLow = Boolean(seg.low_confidence);
      var correctionHtml = corrected.length
        ? '<div class="correction resolved"><svg class="icon icon-xs"><use href="#i-check"/></svg>' +
          '<span>' + corrected.map(function (item) {
            return esc(item.before) + ' → <span class="term">' + esc(item.after) + '</span>';
          }).join('；') + '</span></div>'
        : '';
      return '<article class="segment' + (isLow && !seg.confirmed ? ' low' : '') +
        (seg.confirmed ? ' confirmed' : '') +
        '" data-id="' + esc(seg.id) + '" data-low="' + String(isLow) +
        '" data-confirmed="' + String(Boolean(seg.confirmed)) + '">' +
        '<div class="seg-left">' +
        '<span class="ts">' + esc(fmtClock(seg.start_ms)) + '</span>' +
        (soap === 'u'
          ? ''
          : '<span class="soap ' + soap.toLowerCase() + '"><b>' + esc(sectionLabel(soap)) + '</b></span>') +
        '</div>' +
        '<div class="seg-body">' +
        '<textarea class="seg-input" rows="1" ' + (signed ? 'disabled ' : '') +
        'aria-label="' + esc(t('transcript.title')) + '">' + esc(seg.text) + '</textarea>' +
        (seg.edited ? '<div class="seg-note edited">' + esc(t('segment.edited')) + '</div>' : '') +
        correctionHtml +
        '<div class="conf' + (isLow ? ' low' : '') + '">' +
        '<span class="bar"><i style="width:' + confidence + '%"></i></span>' +
        '<span>' + confidence + '%</span>' +
        '<span class="seg-note">' + esc(seg.confidence_source === 'manual' ? t('conf.manual') : t('conf.global')) + '</span>' +
        '</div>' +
        '</div>' +
        '<div class="seg-actions">' +
        '<button class="mini-btn" type="button" data-seek="' + Number(seg.start_ms || 0) + '">' +
        esc(t('segment.play')) + '</button>' +
        '<button class="mini-btn ' + (seg.confirmed ? 'done' : (isLow ? 'primary' : '')) +
        ' confirm-btn" type="button"' + (signed ? ' disabled' : '') + '>' +
        (seg.confirmed
          ? '<svg class="icon"><use href="#i-check"/></svg>' + esc(t('segment.confirmed'))
          : esc(t('segment.confirm'))) +
        '</button>' +
        '</div>' +
        '</article>';
    }).join('');

    box.querySelectorAll('.seg-input').forEach(function (input) {
      autoGrow(input);
      input.addEventListener('input', function () { autoGrow(input); });
      input.addEventListener('blur', function () {
        var article = input.closest('.segment');
        saveSegment(article.dataset.id, input.value);
      });
    });
    box.querySelectorAll('[data-seek]').forEach(function (btn) {
      btn.addEventListener('click', function () { seek(Number(btn.dataset.seek)); });
    });
    box.querySelectorAll('.confirm-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var article = btn.closest('.segment');
        var confirmed = article.dataset.confirmed === 'true';
        confirmSegment(article.dataset.id, !confirmed);
      });
    });
    applyFilter();
  }

  function applyFilter() {
    var mode = state.filter;
    document.querySelectorAll('#segments .segment').forEach(function (segment) {
      var show = mode === 'all' ||
        (mode === 'low' && segment.dataset.low === 'true' && segment.dataset.confirmed !== 'true') ||
        (mode === 'confirmed' && segment.dataset.confirmed === 'true');
      segment.hidden = !show;
    });
  }

  function renderCorrections() {
    var items = state.detail.corrections || [];
    var box = $('corrections');
    box.hidden = items.length === 0;
    $('corrections-count').textContent = t('corrections.count', { n: items.length });
    if (!items.length) {
      $('corrections-body').innerHTML = '';
      return;
    }
    $('corrections-body').innerHTML =
      '<table><thead><tr>' +
      '<th>' + esc(t('corrections.segment')) + '</th>' +
      '<th>' + esc(t('corrections.before')) + '</th>' +
      '<th>' + esc(t('corrections.after')) + '</th>' +
      '<th>' + esc(t('corrections.rule')) + '</th>' +
      '</tr></thead><tbody>' +
      items.map(function (item) {
        return '<tr><td>' + esc(item.segment_id) + '</td>' +
          '<td><del>' + esc(item.before) + '</del></td>' +
          '<td><ins>' + esc(item.after) + '</ins></td>' +
          '<td>' + esc(item.rule) + '</td></tr>';
      }).join('') +
      '</tbody></table>';
  }

  function renderReport() {
    var report = currentReport();
    var body = $('report-body');
    var gate = $('gate-note');
    var signBtn = $('sign-btn');
    var exportBtn = $('export-btn');
    var signed = isSigned();

    if (!report) {
      body.innerHTML = '<p class="muted">' + esc(t('report.empty')) + '</p>';
      $('completeness-num').textContent = '0';
      $('completeness-bar').style.width = '0%';
      gate.className = 'gate-note';
      gate.innerHTML = '<svg class="icon icon-xs"><use href="#i-alert"/></svg><span>' +
        esc(t('gate.needGenerate')) + '</span>';
      signBtn.disabled = true;
      signBtn.textContent = t('report.sign');
      exportBtn.disabled = true;
      return;
    }

    var reviews = reviewsByKey(report);
    var missing = missingRequired();
    var fields = report.fields || [];

    body.innerHTML = fields.map(function (field) {
      var review = reviews[field.key];
      var value = displayValue(field, review);
      var section = sectionOf(field);
      var mark = section === 'S' ? 's' : section === 'O' ? 'o'
        : section === 'A' ? 'a' : section === 'P' ? 'p' : 'neutral';
      var isMissing = Boolean(field.required) && missing.indexOf(field.key) >= 0;
      var flags = (field.flags || []).filter(function (flag) {
        return flag !== 'missing';
      }).map(function (flag) {
        return '<span class="badge">' + esc(flag) + '</span>';
      }).join('');
      var reviewBadge = '';
      if (review) {
        var cls = review.action === 'accept' ? 'acc'
          : review.action === 'edit' ? 'edit'
          : review.action === 'reject' ? 'rej' : '';
        var label = review.action === 'accept' ? t('report.applied')
          : review.action === 'edit' ? t('report.edited')
          : review.action === 'reject' ? t('report.rejected') : '';
        if (label) reviewBadge = '<span class="badge ' + cls + '">' + esc(label) + '</span>';
      }
      var sources = (field.sources || []).map(function (source) {
        return '<button class="source" type="button" data-seek="' + Number(source.start_ms || 0) + '">' +
          esc(fmtClock(source.start_ms)) + ' ↗</button>';
      }).join('');
      var actions = signed ? '' :
        '<div class="field-actions">' +
        '<button type="button" class="primary" data-review="accept">' + esc(t('report.accept')) + '</button>' +
        '<button type="button" data-review="edit">' + esc(t('report.save')) + '</button>' +
        '<button type="button" data-review="reject">' + esc(t('report.reject')) + '</button>' +
        '</div>';
      var suggestion = signed ? null : (state.detail.suggestions || {})[field.key];
      var suggestionHtml = '';
      var suggestionValue = suggestion ? String(suggestion.value || '').trim() : '';
      var suggestionOptions = suggestion && suggestion.options ? suggestion.options : [];
      // “未提及”不是有信息量的建议，采纳按钮本身即可完成该动作，避免重复噪音。
      var suggestionUseful = suggestion &&
        (suggestionOptions.length > 0 || (suggestionValue && suggestionValue !== '未提及'));
      if (suggestionUseful && !String(value).trim()) {
        var options = (suggestion.options || []).map(function (option) {
          return '<button type="button" class="suggest-chip" data-suggest="' + esc(option) + '">' +
            esc(option) + '</button>';
        }).join('');
        suggestionHtml = '<div class="field-suggest">' +
          (suggestion.value
            ? '<div><b>' + esc(t('report.suggest')) + '：</b>' + esc(suggestion.value) + '</div>'
            : '') +
          (options ? '<div class="field-suggest-row"><span>' + esc(t('report.options')) + '：</span>' +
            options + '</div>' : '') +
          (suggestion.note ? '<div class="field-note info">' + esc(suggestion.note) + '</div>' : '') +
          (suggestion.value
            ? '<button type="button" class="suggest-adopt" data-suggest="' + esc(suggestion.value) + '">' +
              esc(t('report.adopt')) + '</button>'
            : '') +
          '</div>';
      }
      return '<div class="field-group" data-key="' + esc(field.key) + '">' +
        '<div class="field-label">' +
        '<span class="fl-mark ' + mark + '"></span>' +
        '<span>' + esc(field.label || field.key) + '</span>' +
        (field.required ? '<span class="badge req">' + esc(t('report.required')) + '</span>' : '') +
        reviewBadge + flags +
        '</div>' +
        '<div class="field' + (isMissing ? ' missing' : '') +
        (review && review.action === 'reject' ? ' resolved' : '') + '">' +
        '<textarea class="field-input" rows="1" ' + (signed ? 'disabled' : '') +
        'aria-label="' + esc(field.label || field.key) + '"' +
        'placeholder="' + esc(String(value).trim() ? '' : (signed ? t('field.empty') : t('report.notMentioned'))) + '">' +
        esc(value) + '</textarea>' +
        (field.note ? '<div class="field-note">' + esc(field.note) + '</div>' : '') +
        (sources ? '<div class="field-sources">' + sources + '</div>' :
          '<div class="field-note info">' + esc(t('report.noSource')) + '</div>') +
        suggestionHtml +
        actions +
        '</div>' +
        '</div>';
    }).join('');

    body.querySelectorAll('.field-input').forEach(function (input) {
      autoGrow(input);
      input.addEventListener('input', function () { autoGrow(input); });
    });
    body.querySelectorAll('[data-seek]').forEach(function (btn) {
      btn.addEventListener('click', function () { seek(Number(btn.dataset.seek)); });
    });
    body.querySelectorAll('[data-review]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var group = btn.closest('.field-group');
        reviewField(group.dataset.key, btn.dataset.review);
      });
    });
    body.querySelectorAll('[data-suggest]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var group = btn.closest('.field-group');
        var input = group.querySelector('.field-input');
        if (input) input.value = btn.dataset.suggest;
        reviewField(group.dataset.key, 'edit');
      });
    });

    renderCompleteness();
    renderGate();
  }

  function computeCompleteness() {
    var report = currentReport();
    if (!report) return 0;
    var fields = report.fields || [];
    var missingSet = missingRequired();
    var required = fields.filter(function (field) { return field.required; });
    var filled = required.filter(function (field) {
      return missingSet.indexOf(field.key) < 0;
    }).length;
    var requiredRatio = required.length ? filled / required.length : (fields.length ? 1 : 0);
    var lows = lowSegments();
    var confirmedRatio = lows.length
      ? lows.filter(function (seg) { return seg.confirmed; }).length / lows.length
      : 1;
    if (!required.length && !lows.length) return 0;
    return Math.round((requiredRatio * (lows.length ? 0.7 : 1) +
      (lows.length ? confirmedRatio * 0.3 : 0)) * 100);
  }

  function renderCompleteness() {
    var value = isSigned() ? 100 : computeCompleteness();
    $('completeness-num').textContent = String(value);
    $('completeness-bar').style.width = value + '%';
  }

  function renderGate() {
    var gate = $('gate-note');
    var signBtn = $('sign-btn');
    var report = currentReport();
    var signed = isSigned();

    if (!report) return;

    if (signed) {
      signBtn.disabled = true;
      signBtn.textContent = t('status.signed');
      gate.className = 'gate-note ok';
      gate.innerHTML = '<svg class="icon icon-xs"><use href="#i-lock"/></svg><span>' +
        esc(t('gate.signed')) + '</span>';
      $('export-btn').disabled = false;
      return;
    }

    var low = unconfirmedLow().length;
    var missing = missingRequired().length;
    var canSign = low === 0 && missing === 0;
    signBtn.disabled = !canSign;
    signBtn.textContent = t('report.sign');
    $('export-btn').disabled = false;
    gate.className = 'gate-note' + (canSign ? ' ok' : '');

    var message;
    if (canSign) message = t('gate.ready');
    else if (low > 0 && missing > 0) message = t('gate.missingBoth', { low: lowPhrase(low), missing: missing });
    else if (low > 0) message = t('gate.missingLow', { low: lowPhrase(low) });
    else message = t('gate.missingField', { missing: missing });

    gate.innerHTML = '<svg class="icon icon-xs"><use href="#i-' + (canSign ? 'check-circle' : 'alert') +
      '"/></svg><span>' + esc(message) + '</span>';
  }

  function syncLanguageSelect() {
    var select = $('lang-select');
    if (!select) return;
    select.value = state.asrLanguage || 'auto';
    var signed = !!(state.detail && state.detail.session &&
      rank(state.detail.session.status) >= rank('signed'));
    select.disabled = state.busy || state.transcribing || signed;
  }

  function renderPipeline() {
    var session = state.detail.session;
    var status = session.status;
    var level = rank(status);
    var signed = level >= rank('signed');
    $('btn-transcribe').disabled = signed || state.busy || level < rank('recorded');
    $('btn-transcribe').textContent = t(state.transcribing ? 'action.transcribing' : 'action.transcribe');
    $('btn-correct').disabled = signed || state.busy || level < rank('transcribed');
    $('btn-undo').disabled = signed || state.busy ||
      !(state.detail.corrections && state.detail.corrections.length);
    $('btn-extract').disabled = signed || state.busy || level < rank('transcribed');
    $('btn-generate').disabled = signed || state.busy || level < rank('transcribed');
    $('template-select').disabled = signed || state.busy;
    $('model-select').disabled = signed || state.busy || !state.models.length;
    $('btn-record').disabled = signed || state.busy;

    var hint = '';
    if (state.transcribing) hint = t('toast.transcribing');
    else if (level < rank('recorded')) hint = t('transcript.empty');
    else if (level < rank('transcribed')) hint = t('action.transcribe');
    else if (level < rank('generated')) hint = t('action.generate');
    else hint = t('report.title');
    $('pipeline-hint').textContent = hint;
  }

  function renderAll() {
    syncLanguageSelect();
    if (!state.detail) {
      $('content').hidden = true;
      $('empty-state').hidden = false;
      $('session-title').textContent = t('patient.empty');
      $('session-meta').textContent = '';
      $('status-text').textContent = t('status.created');
      $('delete-session').disabled = true;
      $('report-body').innerHTML = '<p class="muted">' + esc(t('report.empty')) + '</p>';
      $('sign-btn').disabled = true;
      $('export-btn').disabled = true;
      $('segments').innerHTML = '';
      $('corrections').hidden = true;
      return;
    }
    $('empty-state').hidden = true;
    $('content').hidden = false;
    renderHeader();
    renderStepper();
    renderMeta();
    renderAudio();
    renderSegments();
    renderCorrections();
    renderReport();
    renderPipeline();
    syncTemplateSelect();
  }

  function syncTemplateSelect() {
    if (!state.detail) return;
    var session = state.detail.session;
    if (session.template_id) $('template-select').value = session.template_id;
  }

  function setDrawer(open) {
    document.querySelector('.rail').classList.toggle('open', open);
    $('drawer-backdrop').hidden = !open;
  }

  /* ------------------------------------------------------------- data load */
  async function loadTemplates() {
    try {
      state.templates = await api('/templates');
    } catch (e) {
      state.templates = [{ id: 'general_soap', name: '通用 SOAP' }];
    }
    if (!state.templates.length) state.templates = [{ id: 'general_soap', name: '通用 SOAP' }];
    $('template-select').innerHTML = state.templates.map(function (template) {
      return '<option value="' + esc(template.id) + '">' +
        esc(template.name || template.id) + '</option>';
    }).join('');
    syncTemplateSelect();
  }

  function modelLabel(idOrModel) {
    var id = typeof idOrModel === 'string' ? idOrModel : idOrModel.id;
    if (id === 'caps-qwen3') return 'Qwen3-ASR';
    return String(id || '').replace(/^./, function (ch) { return ch.toUpperCase(); });
  }

  function modelNote(model) {
    if (model.kind === 'caps') {
      return model.installed ? t('ext.installed') : t('ext.uninstalled');
    }
    return model.local ? t('model.localSuffix') : t('ext.uninstalled');
  }

  function showExtDialog(model) {
    var info = model.install || {};
    $('ext-name').textContent = model.label || modelLabel(model);
    $('ext-size').textContent = info.size_mb ? (info.size_mb / 1024).toFixed(1) + ' GB' : '—';
    $('ext-sha').textContent = info.sha256 || '—';
    var url = info.url || '';
    $('ext-url').textContent = url || '—';
    $('ext-url').href = url || '#';
    $('ext-steps').innerHTML = (info.steps || []).map(function (step) {
      return '<li>' + esc(step) + '</li>';
    }).join('');
    $('ext-note').textContent = info.note || '';
    var copyBtn = $('ext-copy');
    if (copyBtn) {
      copyBtn.disabled = !url;
      copyBtn.classList.remove('done');
      copyBtn.textContent = t('ext.copy');
    }
    state.extUrl = url;
    var dialog = $('ext-dialog');
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }

  function closeExtDialog() {
    var dialog = $('ext-dialog');
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  }

  async function loadModels() {
    try {
      var info = await api('/api/asr/models');
      state.models = info.supported || [];
      state.asrModel = info.current || '';
      state.asrDevice = info.device || '';
    } catch (e) {
      // 模型列表拿不到不阻塞主流程；下拉显示为空并禁用，转写仍可用默认模型
      state.models = [];
      state.asrModel = '';
      state.asrDevice = '';
    }
    renderModelSelect();
  }

  function renderModelSelect() {
    var select = $('model-select');
    if (!select) return;
    if (!state.models.length) {
      select.innerHTML = '<option value=""></option>';
      select.disabled = true;
      select.title = '';
      return;
    }
    // Qwen3-ASR 作为可选高精度档，排在列表最末，避免抢默认项的注意力
    var ordered = state.models.slice().sort(function (a, b) {
      return (a.id === 'caps-qwen3' ? 1 : 0) - (b.id === 'caps-qwen3' ? 1 : 0);
    });
    select.innerHTML = ordered.map(function (model) {
      return '<option value="' + esc(model.id) + '">' +
        esc(modelLabel(model) + modelNote(model)) + '</option>';
    }).join('');
    if (state.asrModel) select.value = state.asrModel;
    select.disabled = state.busy;
    select.title = t('action.modelAria') +
      (state.asrDevice ? ' · ' + state.asrDevice.toUpperCase() : '');
  }

  async function switchModel(model) {
    if (!model || model === state.asrModel) return;
    var label = modelLabel(model);
    state.busy = true;
    renderModelSelect();
    $('pipeline-hint').textContent = t('model.switching', { name: label });
    try {
      var result = await api('/api/asr/model', jsonOptions('POST', { model: model }));
      state.asrModel = result.model || model;
      showToast(t('model.switched', { name: label, s: result.elapsed == null ? 0 : result.elapsed }));
    } catch (e) {
      $('model-select').value = state.asrModel;
      fail(e);
    } finally {
      state.busy = false;
      renderModelSelect();
      if (state.detail) renderPipeline();
    }
  }

  async function loadSessions() {
    try {
      var query = ($('session-search').value || '').trim();
      var path = '/sessions?limit=50' + (query ? '&q=' + encodeURIComponent(query) : '');
      state.sessions = await api(path);
      renderSessions();
    } catch (e) {
      fail(e);
    }
  }

  async function refresh() {
    if (!state.sessionId) { renderAll(); return; }
    state.detail = await api('/sessions/' + encodeURIComponent(state.sessionId));
    renderAll();
  }

  async function selectSession(id) {
    state.sessionId = id;
    state.filter = 'all';
    document.querySelectorAll('.filters [data-filter]').forEach(function (btn) {
      btn.setAttribute('aria-pressed', String(btn.dataset.filter === 'all'));
    });
    try {
      await refresh();
      renderSessions();
      setDrawer(false);
    } catch (e) {
      fail(e);
    }
  }

  async function createSession() {
    if (state.busy) return;
    state.busy = true;
    try {
      var created = await api('/sessions', jsonOptions('POST', {
        template_id: $('template-select').value || '',
        title: ''
      }));
      state.busy = false;
      showToast(t('toast.created'));
      await loadSessions();
      await selectSession(created.id);
    } catch (e) {
      state.busy = false;
      fail(e);
    }
  }

  async function deleteSession() {
    if (!state.sessionId) return;
    if (!window.confirm(t('toast.confirmDelete'))) return;
    try {
      await api('/sessions/' + encodeURIComponent(state.sessionId), { method: 'DELETE' });
      state.sessionId = null;
      state.detail = null;
      renderAll();
      await loadSessions();
      showToast(t('toast.deleted'));
    } catch (e) {
      fail(e);
    }
  }

  /* -------------------------------------------------------------- segments */
  async function saveSegment(id, text) {
    if (!state.detail) return;
    var segment = (state.detail.segments || []).filter(function (seg) { return seg.id === id; })[0];
    if (!segment || segment.text === text) return;
    try {
      await api('/sessions/' + encodeURIComponent(state.sessionId) + '/segments/' +
        encodeURIComponent(id), jsonOptions('PATCH', { text: text }));
      showToast(t('toast.saved'));
      await refresh();
    } catch (e) {
      fail(e);
    }
  }

  async function confirmSegment(id, confirmed) {
    try {
      await api('/sessions/' + encodeURIComponent(state.sessionId) + '/segments/' +
        encodeURIComponent(id), jsonOptions('PATCH', { confirmed: confirmed }));
      await refresh();
    } catch (e) {
      fail(e);
    }
  }

  /* ---------------------------------------------------------------- report */
  async function reviewField(key, action) {
    var report = currentReport();
    if (!report) return;
    var group = document.querySelector('.field-group[data-key="' + CSS.escape(key) + '"]');
    var input = group ? group.querySelector('.field-input') : null;
    var value = input ? input.value : null;
    if (action === 'edit' && !String(value || '').trim()) {
      showToast(t('report.pending'));
      return;
    }
    try {
      await api('/reports/' + encodeURIComponent(report.id) + '/fields/' + encodeURIComponent(key),
        jsonOptions('PATCH', { action: action, value: value, reviewer: 'demo-doctor' }));
      showToast(action === 'accept' ? t('toast.accepted')
        : action === 'edit' ? t('toast.edited') : t('toast.rejected'));
      await refresh();
    } catch (e) {
      fail(e);
    }
  }

  /* ---------------------------------------------------------------- action */
  var TRANSCRIBE_TIMEOUT_MS = 15 * 60 * 1000;

  function pollTranscribe(jobId) {
    return new Promise(function (resolve, reject) {
      var started = Date.now();
      function tick() {
        api('/transcribe-jobs/' + encodeURIComponent(jobId)).then(function (job) {
          if (job.status === 'running') {
            updateTranscribeProgress(job);
            if (Date.now() - started > TRANSCRIBE_TIMEOUT_MS) {
              reject(new Error(t('toast.transcribeTimeout')));
              return;
            }
            setTimeout(tick, 500);
            return;
          }
          updateTranscribeProgress(job);
          if (job.status === 'error') {
            reject(new Error(job.error || t('toast.error', { msg: '' })));
            return;
          }
          resolve(job);
        }).catch(function (err) {
          // 轮询偶发连接重置：在总超时内继续重试，避免一次抖动中断整个转写
          if (Date.now() - started > TRANSCRIBE_TIMEOUT_MS) { reject(err); return; }
          setTimeout(tick, 900);
        });
      }
      tick();
    });
  }

  async function doTranscribe() {
    if (!state.sessionId) { showToast(t('toast.noSession')); return; }
    state.busy = true;
    state.transcribing = true;
    state.transcribeStartedAt = Date.now();
    state.transcribeJob = { progress: 0.02, phase: 'prepare', processed: 0, total: 0 };
    $('pipeline-hint').textContent = t('toast.transcribing');
    renderPipeline();
    renderSegments();
    startProgressTicker();
    try {
      var job = await api('/sessions/' + encodeURIComponent(state.sessionId) + '/transcribe-jobs',
        jsonOptions('POST', {
          language: state.asrLanguage, beam_size: 5, asr_template: 'soap'
        }));
      var done = await pollTranscribe(job.job_id);
      state.transcribing = false;
      showToast(t('toast.transcribed', {
        n: (done.result && done.result.segments) || 0,
        low: (done.result && done.result.low_confidence) || 0
      }));
      await refresh();
      await loadSessions();
    } catch (e) {
      state.transcribing = false;
      fail(e);
      try { await refresh(); } catch (ignored) { /* 保留当前界面 */ }
    } finally {
      stopProgressTicker();
      state.busy = false;
      state.transcribeJob = null;
      state.transcribeStartedAt = null;
      if (state.detail) renderPipeline();
    }
  }

  async function doCorrect() {
    if (!state.sessionId) { showToast(t('toast.noSession')); return; }
    try {
      var result = await api('/sessions/' + encodeURIComponent(state.sessionId) + '/correct-terms',
        jsonOptions('POST', {}));
      showToast(t('toast.corrected', { n: result.corrections.length }));
      await refresh();
    } catch (e) {
      fail(e);
    }
  }

  async function undoCorrect() {
    if (!state.sessionId) { showToast(t('toast.noSession')); return; }
    try {
      var result = await api('/sessions/' + encodeURIComponent(state.sessionId) + '/correct-terms/undo',
        jsonOptions('POST', {}));
      showToast(t('toast.undone', { n: result.restored_segments }));
      await refresh();
    } catch (e) {
      fail(e);
    }
  }

  async function doExtract() {
    if (!state.sessionId) { showToast(t('toast.noSession')); return; }
    try {
      var result = await api('/sessions/' + encodeURIComponent(state.sessionId) + '/extract',
        jsonOptions('POST', { deidentify: false, language_mode: 'bilingual' }));
      showToast(t('toast.extracted', { n: result.elements.length }));
      await refresh();
    } catch (e) {
      fail(e);
    }
  }

  async function doGenerate() {
    if (!state.sessionId) { showToast(t('toast.noSession')); return; }
    var templateId = $('template-select').value || '';
    state.busy = true;
    renderPipeline();
    try {
      var result = await api('/sessions/' + encodeURIComponent(state.sessionId) + '/generate',
        jsonOptions('POST', {
          template_id: templateId,
          deidentify: false,
          language_mode: 'bilingual',
          keep_unclassified: true
        }));
      showToast(t('toast.generated', { n: (result.report.fields || []).length }));
      await refresh();
      await loadSessions();
    } catch (e) {
      fail(e);
    } finally {
      state.busy = false;
      if (state.detail) renderPipeline();
    }
  }

  /* ------------------------------------------------------------------ sign */
  function renderSignChecklist() {
    var low = unconfirmedLow().length;
    var missing = missingRequired().length;
    var rows = [];
    rows.push(checkRow(low === 0,
      low === 0 ? t('dialog.checkLow') : t('dialog.checkLowPending', { n: low })));
    rows.push(checkRow(missing === 0,
      missing === 0 ? t('dialog.checkRequired') : t('dialog.checkRequiredPending', { n: missing })));
    rows.push(checkRow(true, t('dialog.checkTrace')));
    $('sign-checklist').innerHTML = rows.join('');
  }

  function checkRow(ok, text) {
    return '<div class="check-item">' +
      '<svg class="icon icon-xs" style="color:var(--' + (ok ? 'success' : 'warn') + ')">' +
      '<use href="#i-' + (ok ? 'check-circle' : 'alert') + '"/></svg>' +
      '<span>' + esc(text) + '</span></div>';
  }

  async function signReport() {
    var report = currentReport();
    if (!report) { showToast(t('toast.noReport')); return; }
    try {
      var result = await api('/reports/' + encodeURIComponent(report.id) + '/sign',
        jsonOptions('POST', { signed_by: 'demo-doctor' }));
      if (result.ok) {
        $('sign-dialog').close();
        showToast(t('toast.signed'));
        await refresh();
        await loadSessions();
      } else {
        $('sign-dialog').close();
        showToast(t('toast.blocked', { why: (result.blocked_by || []).join('、') }));
      }
    } catch (e) {
      fail(e);
    }
  }

  /* ---------------------------------------------------------------- export */
  function exportPdfHtml() {
    var report = currentReport();
    if (!report) return '';
    var session = state.detail.session;
    var reviews = reviewsByKey(report);
    var groups = { S: [], O: [], A: [], P: [], U: [] };
    (report.fields || []).forEach(function (field) {
      groups[sectionOf(field)].push(field);
    });
    var sections = ['S', 'O', 'A', 'P', 'U'].map(function (section) {
      var list = groups[section];
      if (!list.length) return '';
      return '<div class="doc-section"><div class="doc-label">' + esc(sectionLabel(section)) +
        '</div>' + list.map(function (field) {
          var value = displayValue(field, reviews[field.key]);
          if (!String(value).trim()) value = t('field.empty');
          return '<div class="doc-value"><b>' + esc(field.label || field.key) + '：</b>' +
            esc(value) + '</div>';
        }).join('') + '</div>';
    }).join('');

    return '<div class="doc-brand"><span class="doc-mark">M</span><span>MedScribe</span></div>' +
      '<div class="doc-title">' + esc(t('report.title')) + '</div>' +
      '<div class="doc-meta">' + esc(session.title || session.id) + ' · ' +
      esc(t('status.' + session.status)) + '</div>' +
      '<div class="doc-meta">' + esc(session.id) + ' · ' +
      esc(new Date(session.created_at).toLocaleString()) + '</div>' +
      '<div class="doc-rule"></div>' + sections +
      '<div class="doc-foot">' + esc(t('export.real')) + ' · MedScribe HK</div>';
  }

  async function renderExportPreview() {
    var report = currentReport();
    if (!report) return;
    $('export-pdf-preview').innerHTML = exportPdfHtml();
    var isPdf = state.exportFormat === 'pdf';
    $('export-pdf-preview').hidden = !isPdf;
    $('export-json-preview').hidden = isPdf;
    $('download-export').textContent = isPdf ? t('export.download') : t('export.downloadJson');
    document.querySelectorAll('[data-export-tab]').forEach(function (btn) {
      btn.setAttribute('aria-selected', String(btn.dataset.exportTab === state.exportFormat));
    });
    if (!isPdf) {
      $('export-json-preview').textContent = '…';
      try {
        var data = await api('/reports/' + encodeURIComponent(report.id) +
          '/preview?format=json&language_mode=bilingual');
        $('export-json-preview').textContent =
          typeof data === 'string' ? data : JSON.stringify(data, null, 2);
      } catch (e) {
        $('export-json-preview').textContent = e.message;
      }
    }
  }

  async function openExport() {
    var report = currentReport();
    if (!report) { showToast(t('toast.noReport')); return; }
    state.exportFormat = 'pdf';
    renderExportPreview();
    if ($('export-dialog').showModal) $('export-dialog').showModal();
  }

  async function downloadExport() {
    var report = currentReport();
    if (!report) return;
    var format = state.exportFormat;
    try {
      var result = await api('/reports/' + encodeURIComponent(report.id) + '/export',
        jsonOptions('POST', { formats: [format], language_mode: 'bilingual' }));
      var record = (result.records || []).filter(function (item) {
        return item.format === format && item.local_path;
      })[0];
      if (!record) {
        var skipped = (result.skipped || []).filter(function (item) {
          return item.format === format;
        })[0];
        showToast(skipped && skipped.reason ? skipped.reason : t('toast.error', { msg: format }));
        return;
      }
      var link = document.createElement('a');
      link.href = '/exports/' + encodeURIComponent(record.local_path);
      link.download = record.local_path;
      document.body.appendChild(link);
      link.click();
      link.remove();
      showToast(t('toast.exported', { fmt: format.toUpperCase() }));
      await refresh();
    } catch (e) {
      fail(e);
    }
  }

  /* ----------------------------------------------------------------- audio */
  var player = $('player');
  var playBtn = $('play-btn');

  function setPlaying(playing) {
    state.playing = playing;
    playBtn.setAttribute('aria-pressed', String(playing));
    playBtn.classList.toggle('is-playing', playing);
    $('waveform').classList.toggle('playing', playing);
    playBtn.setAttribute('aria-label', t(playing ? 'audio.pause' : 'audio.play'));
  }

  function renderAudio() {
    var session = state.detail ? state.detail.session : null;
    var hasAudio = Boolean(session && session.audio_path);
    playBtn.disabled = !hasAudio;
    if (hasAudio) {
      var desired = '/sessions/' + encodeURIComponent(session.id) + '/audio';
      if (player.getAttribute('src') !== desired) {
        player.src = desired;
        setPlaying(false);
      }
    } else {
      player.removeAttribute('src');
      player.load();
      setPlaying(false);
    }
    if (!state.playing) {
      $('audio-time').textContent = '00:00 / ' + fmtClock(session ? session.duration_ms : 0);
    }
    // 文件名展示在波形与时长之间，方便确认导入的是哪一段录音
    var fileEl = $('audio-file');
    if (fileEl) {
      var name = (session && session.audio_filename) || state.pendingAudioName || '';
      if (name) {
        fileEl.textContent = name;
        fileEl.title = name;
        fileEl.hidden = false;
      } else {
        fileEl.textContent = '';
        fileEl.removeAttribute('title');
        fileEl.hidden = true;
      }
    }
  }

  function seek(ms) {
    if (!state.detail || !state.detail.session.audio_path) {
      showToast(t('toast.noAudio'));
      return;
    }
    try {
      player.currentTime = Math.max(0, (ms || 0) / 1000);
      if (player.paused) player.play().catch(function () {});
    } catch (e) { /* ignore */ }
  }

  async function toggleRecord() {
    var button = $('btn-record');
    if (state.recorder && state.recorder.state === 'recording') {
      state.recorder.stop();
      return;
    }
    if (!state.sessionId) { showToast(t('toast.noSession')); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showToast(t('toast.noAudio'));
      return;
    }
    try {
      var stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      state.chunks = [];
      state.recSeconds = 0;
      var recorder = new MediaRecorder(stream);
      state.recorder = recorder;
      recorder.ondataavailable = function (event) {
        if (event.data && event.data.size) state.chunks.push(event.data);
      };
      recorder.onstop = async function () {
        stream.getTracks().forEach(function (track) { track.stop(); });
        clearInterval(state.recTimer);
        button.classList.remove('recording');
        $('record-label').textContent = t('audio.record');
        var blob = new Blob(state.chunks, { type: state.chunks[0] ? state.chunks[0].type : 'audio/webm' });
        if (blob.size < 2000) { showToast(t('toast.noAudio')); return; }
        var ext = blob.type.indexOf('ogg') >= 0 ? 'ogg' : 'webm';
        await uploadAudio(blob, 'recording.' + ext);
      };
      recorder.start();
      button.classList.add('recording');
      $('record-label').textContent = t('audio.stop');
      showToast(t('toast.recording'));
      state.recTimer = setInterval(function () {
        state.recSeconds += 1;
        $('audio-time').textContent = fmtClock(state.recSeconds * 1000) +
          ' / ' + fmtClock(state.detail ? state.detail.session.duration_ms : 0);
      }, 1000);
    } catch (e) {
      fail(e);
    }
  }

  async function uploadAudio(blob, name) {
    if (!state.sessionId) { showToast(t('toast.noSession')); return; }
    var form = new FormData();
    form.append('file', blob, name);
    state.pendingAudioName = name || '';
    renderAudio();
    try {
      var result = await api('/sessions/' + encodeURIComponent(state.sessionId) + '/audio',
        { method: 'POST', body: form });
      showToast(t('toast.uploaded', { dur: fmtClock(result.duration_ms) }));
      state.pendingAudioName = '';
      await refresh();
      await loadSessions();
    } catch (e) {
      state.pendingAudioName = '';
      fail(e);
    }
  }

  function uploadFile(input) {
    var file = input.files && input.files[0];
    if (!file) return;
    uploadAudio(file, file.name);
    input.value = '';
  }

  /* ---------------------------------------------------------------- health */
  async function loadHealth() {
    var pill = $('health-pill');
    try {
      var health = await api('/health');
      var ok = Boolean(health.ok);
      $('health-label').textContent = t(ok ? 'health.ok' : 'health.fail');
      $('health-summary').textContent = t(ok ? 'health.ok' : 'health.fail');
      pill.classList.toggle('bad', !ok);
      ['asr', 'engine'].forEach(function (name) {
        var component = (health.components || []).filter(function (item) {
          return item.name === name;
        })[0];
        var dot = $('dot-' + name);
        if (!dot) return;
        dot.className = 'dot ' + (component && component.ok ? 'ok' : 'warn');
        if (component && !component.ok && component.error) {
          dot.parentElement.title = component.error;
        }
      });
    } catch (e) {
      $('health-label').textContent = t('health.down');
      $('health-summary').textContent = t('health.down');
      pill.classList.add('bad');
    }
  }

  /* ---------------------------------------------------------------- language */
  function applyLanguage(lang) {
    state.lang = lang === 'en' ? 'en' : (lang === 'zh-Hant' ? 'zh-Hant' : 'zh');
    try { localStorage.setItem(LANG_KEY, state.lang); } catch (e) {}
    root.lang = state.lang === 'en' ? 'en' : (state.lang === 'zh-Hant' ? 'zh-Hant' : 'zh-Hans');
    root.dataset.lang = state.lang;
    document.title = state.lang === 'en' ? 'MedScribe HK · Clinical notes'
      : state.lang === 'zh-Hant' ? 'MedScribe HK · 臨床病歷助手'
      : 'MedScribe HK · 口述病歷助手';

    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      el.textContent = t(el.dataset.i18n);
    });
    document.querySelectorAll('[data-i18n-aria]').forEach(function (el) {
      el.setAttribute('aria-label', t(el.dataset.i18nAria));
    });
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      el.setAttribute('title', t(el.dataset.i18nTitle));
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      el.setAttribute('placeholder', t(el.dataset.i18nPlaceholder));
    });
    document.querySelectorAll('[data-lang-set]').forEach(function (btn) {
      btn.setAttribute('aria-pressed', String(btn.dataset.langSet === state.lang));
    });

    var mobileLang = $('mobile-lang');
    if (mobileLang) {
      var nextLang = state.lang === 'zh' ? 'zh-Hant' : (state.lang === 'zh-Hant' ? 'en' : 'zh');
      mobileLang.textContent = nextLang === 'zh-Hant' ? '繁' : (nextLang === 'en' ? 'EN' : '简');
      mobileLang.setAttribute('aria-label', t('language.switch'));
      mobileLang.setAttribute('title', t('language.switch'));
    }

    applyTheme(root.dataset.themeMode || 'system', false);
    $('record-label').textContent = t('audio.record');
    renderSessions();
    renderAll();
    renderModelSelect();
    loadHealth();
    if ($('export-dialog').open) renderExportPreview();
  }

  /* ------------------------------------------------------------------ wiring */
  document.querySelectorAll('[data-theme-set]').forEach(function (btn) {
    btn.addEventListener('click', function () { applyTheme(btn.dataset.themeSet, true); });
  });
  $('model-select').addEventListener('change', function () {
    var picked = state.models.filter(function (model) { return model.id === this.value; }.bind(this))[0];
    if (!picked) return;
    if (!picked.installed) {
      showExtDialog(picked);
      this.value = state.asrModel;
      return;
    }
    switchModel(this.value);
  });
  $('ext-close').addEventListener('click', closeExtDialog);

  $('ext-copy').addEventListener('click', function () {
    var url = state.extUrl || '';
    if (!url) return;
    var button = $('ext-copy');
    var done = function () {
      button.classList.add('done');
      button.textContent = t('ext.copied');
      setTimeout(function () {
        button.classList.remove('done');
        button.textContent = t('ext.copy');
      }, 1800);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(done, function () { showToast(t('toast.error', { msg: '' })); });
      return;
    }
    var temp = document.createElement('textarea');
    temp.value = url;
    document.body.appendChild(temp);
    temp.select();
    try {
      document.execCommand('copy');
      done();
    } catch (e) {
      showToast(t('toast.error', { msg: '' }));
    }
    document.body.removeChild(temp);
  });
  $('mobile-theme').addEventListener('click', cycleTheme);

  document.querySelectorAll('[data-lang-set]').forEach(function (btn) {
    btn.addEventListener('click', function () { applyLanguage(btn.dataset.langSet); });
  });
  $('mobile-lang').addEventListener('click', function () {
    var order = ['zh', 'zh-Hant', 'en'];
    applyLanguage(order[(order.indexOf(state.lang) + 1) % order.length]);
  });

  $('new-session').addEventListener('click', createSession);
  $('empty-new').addEventListener('click', createSession);
  $('delete-session').addEventListener('click', deleteSession);
  $('mobile-sessions').addEventListener('click', function () {
    setDrawer(!document.querySelector('.rail').classList.contains('open'));
  });
  $('drawer-backdrop').addEventListener('click', function () { setDrawer(false); });

  $('session-list').addEventListener('click', function (event) {
    var item = event.target.closest('[data-session-id]');
    if (item) selectSession(item.dataset.sessionId);
  });

  var searchTimer = null;
  $('session-search').addEventListener('input', function () {
    renderSessions();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadSessions, 300);
  });

  document.querySelectorAll('.filters [data-filter]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      state.filter = btn.dataset.filter;
      document.querySelectorAll('.filters [data-filter]').forEach(function (other) {
        other.setAttribute('aria-pressed', String(other === btn));
      });
      applyFilter();
    });
  });

  document.querySelectorAll('#stepper .step').forEach(function (step) {
    step.addEventListener('click', function () {
      if (!step.classList.contains('current') && !step.classList.contains('done')) {
        showToast(t('gate.needGenerate'));
      }
    });
  });

  $('btn-transcribe').addEventListener('click', doTranscribe);
  $('lang-select').addEventListener('change', function () {
    state.asrLanguage = this.value || 'auto';
    try { localStorage.setItem(ASR_LANG_KEY, state.asrLanguage); } catch (e) {}
  });
  $('btn-correct').addEventListener('click', doCorrect);
  $('btn-undo').addEventListener('click', undoCorrect);
  $('btn-extract').addEventListener('click', doExtract);
  $('btn-generate').addEventListener('click', doGenerate);
  $('btn-record').addEventListener('click', toggleRecord);
  $('file-input').addEventListener('change', function () { uploadFile(this); });

  playBtn.addEventListener('click', function () {
    if (!state.detail || !state.detail.session.audio_path) {
      showToast(t('toast.noAudio'));
      return;
    }
    if (player.paused) player.play().catch(fail);
    else player.pause();
  });
  player.addEventListener('play', function () { setPlaying(true); });
  player.addEventListener('pause', function () { setPlaying(false); });
  player.addEventListener('ended', function () { setPlaying(false); });
  player.addEventListener('timeupdate', function () {
    var duration = player.duration && isFinite(player.duration)
      ? player.duration : (state.detail ? state.detail.session.duration_ms / 1000 : 0);
    $('audio-time').textContent = fmtClock(player.currentTime * 1000) + ' / ' + fmtClock(duration * 1000);
  });

  $('speed-btn').addEventListener('click', function () {
    var rates = [1, 1.25, 1.5];
    var current = rates.indexOf(player.playbackRate);
    var next = rates[(current >= 0 ? current + 1 : 0) % rates.length];
    player.playbackRate = next;
    $('speed-btn').textContent = next.toFixed(1) + '×';
  });

  $('sign-btn').addEventListener('click', function () {
    if ($('sign-btn').disabled) return;
    renderSignChecklist();
    $('consent-check').checked = false;
    $('confirm-sign').disabled = true;
    if ($('sign-dialog').showModal) $('sign-dialog').showModal();
  });
  $('close-dialog').addEventListener('click', function () { $('sign-dialog').close(); });
  $('cancel-sign').addEventListener('click', function () { $('sign-dialog').close(); });
  $('consent-check').addEventListener('change', function () {
    $('confirm-sign').disabled = !$('consent-check').checked;
  });
  $('confirm-sign').addEventListener('click', signReport);

  $('export-btn').addEventListener('click', openExport);
  document.querySelectorAll('[data-export-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      state.exportFormat = btn.dataset.exportTab === 'json' ? 'json' : 'pdf';
      renderExportPreview();
    });
  });
  $('close-export').addEventListener('click', function () { $('export-dialog').close(); });
  $('cancel-export').addEventListener('click', function () { $('export-dialog').close(); });
  $('download-export').addEventListener('click', downloadExport);

  var healthWrap = $('health-wrap');
  $('health-pill').addEventListener('click', function () {
    var willOpen = $('health-popover').hidden;
    $('health-popover').hidden = !willOpen;
    $('health-pill').setAttribute('aria-expanded', String(willOpen));
  });
  document.addEventListener('click', function (event) {
    if (!healthWrap.contains(event.target)) {
      $('health-popover').hidden = true;
      $('health-pill').setAttribute('aria-expanded', 'false');
    }
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      $('health-popover').hidden = true;
      $('health-pill').setAttribute('aria-expanded', 'false');
      setDrawer(false);
    }
  });

  $('help-btn').addEventListener('click', function () {
    if ($('help-dialog').showModal) $('help-dialog').showModal();
  });
  $('close-help').addEventListener('click', function () { $('help-dialog').close(); });
  $('help-ok').addEventListener('click', function () { $('help-dialog').close(); });
  $('more-btn').addEventListener('click', function () {
    showToast(state.lang === 'en' ? 'No additional actions yet.'
      : state.lang === 'zh-Hant' ? '暫無更多操作。' : '暂无更多操作。');
  });

  document.addEventListener('keydown', function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    var tag = document.activeElement ? document.activeElement.tagName : '';
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    if (event.key === '/') {
      event.preventDefault();
      $('session-search').focus();
      return;
    }
    if (event.key.toLowerCase() === 't') cycleTheme();
  });

  if (media.addEventListener) {
    media.addEventListener('change', function () {
      if ((root.dataset.themeMode || 'system') === 'system') applyTheme('system', false);
    });
  }

  var waveform = $('waveform');
  for (var i = 0; i < 54; i += 1) {
    var bar = document.createElement('span');
    var height = 16 + Math.round(Math.abs(Math.sin(i * 0.72)) * 54 + Math.abs(Math.cos(i * 0.21)) * 18);
    bar.style.height = Math.min(94, height) + '%';
    bar.style.animationDelay = (i * -0.035) + 's';
    waveform.appendChild(bar);
  }

  /* -------------------------------------------------------------------- boot */
  function reportBootFailure(err) {
    try { console.error(err); } catch (e) {}
    var box = document.createElement('div');
    box.className = 'boot-failure';
    box.setAttribute('role', 'alert');
    box.textContent = '界面加载不完整：请按 Ctrl+F5 强制刷新；若仍异常，请重启服务后再打开页面。';
    document.body.appendChild(box);
  }

  var initialTheme = 'system';
  try { initialTheme = localStorage.getItem(THEME_KEY) || 'system'; } catch (e) {}
  try { state.asrLanguage = localStorage.getItem(ASR_LANG_KEY) || 'auto'; } catch (e) {}
  var initialLang = 'zh';
  try { initialLang = localStorage.getItem(LANG_KEY) || 'zh'; } catch (e) {}

  try {
    applyTheme(initialTheme, false);
    applyLanguage(initialLang);
    renderAll();
    loadTemplates();
    loadModels();
    loadHealth();
    loadSessions();
    setInterval(loadHealth, 30000);
  } catch (err) {
    reportBootFailure(err);
  }
})();
