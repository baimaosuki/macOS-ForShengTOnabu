# MI 黑屏：已证实进展与尚未闭合的 PM 等待

完整正文 seq10093，621172字节/3382行，双slot有效，未覆写；逐行报告为mi-full-log-review.json。
新策略确实放行loginwindow UUID，151.170秒exec；199.442秒display on；240.035/240.403秒两个黑色BGRA帧已完成scanout和原生完成，last_displayed_id=2，queue_head=0。
layer1/2是明确clear，不能再归因为漏合成了有内容的layer；非阻塞swap_wait Busy仍不能伪改成功。

loginwindow109/tid476在209.392484166秒call2259向powerd46发送msgid73023，remote3a23/receive903，正文279秒结束前没有返回或下一次trap闭合记录。
观察器为已准入loginwindow调用保留完成记录，未见该tid的drop；日志lock_lost14仍是取证限制，不能声称已拿到powerd现场栈。

匹配25F71 cache UUID46e0097ff38536c884a9a40d315a32d1中的IOKit反汇编确认：
- _io_pm_hid_event_report_activity = unslid0x184862ebc。
- 0x184862f0c/10从0x184918fb0加载header常量000000003f1d0100，消息ID=73023。
- 0x184862f44：mov w2,#0x24（请求36字节）。
- 0x184862f48：mov w3,#0x30（接收上限48字节）。
- 0x184862f4c：mov w5,#0；0x184862f50：mov w6,#0；随后调用Mach消息包装。
完整指令见powerd73023-disassembly.txt。这个未返回请求不是大体积XPC。

六条too-large分别来自launchd、opendirectoryd和早期in-transit端口，并无证据指向tccd或iconservices；主线程随后在209秒继续执行，故这些较早记录本身不是最终永久等待点。

原25F71 launchd job中tccd的daemon/agent标签分别为com.apple.tccd.system与com.apple.tccd；iconservices代理实际为com.apple.iconservices.iconservicesagent，系统/用户域同名；lsd系统/用户域也同名com.apple.lsd。
上个策略已覆盖这些基础标签。当前新增com.apple.iconservicesd/agent别名及lsd前缀是用户明确要求的完整放行扩展，不能当作已证实的powerd修复。

公开PowerManagement main的HIDEventWatcher.c提供同名服务端方法，pmconfigd.m将MIG监听和初始化放在同一PM队列；它说明初始化/队列阻塞可能让多个PM请求等待，但并非25F71 powerd运行时现场证据。
本机powerd原文件从原OS镜像只读提取，SHA2566657ac506c322a04d058c18663998bdb27ca48e7984bde56a97a89027dfc65c7，已剥符号，不能凭静态候选认定卡在某个缺失硬件或被挂起服务。
下一项真正PM功能修复必须由powerd接收队列/初始化阻塞位置支撑，恢复真实依赖或正确处理缺失硬件；不能给73023假回成功、伪造输入活动或改掉全部RPC超时。

本次只补powerd原始非暂停快照：发现后0/60/120秒；其他目标仍0/150/300秒，不改MIG请求/返回/超时。单独签名boot-tools CI；原native内核及白名单CI独立保留。11项主机测试和17子用例通过，不能预先声称已修复黑屏。
