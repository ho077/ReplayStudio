# 学习路线图

按项目阶段排列，每篇文档都引用本仓库真实代码 + 可运行小实验。

1. **桌面应用基础** → `learning/01-desktop-app-basics.md`（待补）
   Qt 事件循环、信号槽、为什么 UI 线程不能阻塞。
2. **媒体播放** → `learning/02-media-playback.md`
   libmpv、解封装/解码、硬解 vs 软解、跨线程信号纪律。
3. **视频索引** → `learning/03-video-indexing.md`
   ACLOS 加密格式、SQLite 索引设计、增量扫描。
4. **播放器控制** → `learning/04-player-controls.md`
   一个按钮到画面的完整链路、音轨/字幕/比例、播放列表与连播、
   拖放与「打开方式」。
5. **主题与界面换肤** → `learning/05-theming.md`（待补）
   QSS 变量替身、注册式内联样式、alpha 通道顺序陷阱。
6. **录屏** → `learning/06-screen-recording.md`（Phase 3 时编写）
   obs-websocket、编码器、码率/帧率权衡。

配套动手任务在每篇文档末尾：入门（改几行看效果）/ 进阶（理解数据流）/
挑战（修一个真实问题）。
