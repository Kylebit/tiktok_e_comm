# 本机单用户与唯一终审：私有可运行首段

固定父提交2ea7e3fd；只在显式synthetic ReleaseStore运行。默认没有真实HTTP路由、浏览器启动、cookie、provider或worker。未修改既有legacy guard，也不提供裸approve兼容。实际本机grant/终审/恢复可运行，不把private FINAL_REVIEW_READY或APPROVED称作正式业务权威。

## 来源与边界

用户已经决定信任同Windows用户下的程序。本段producer读取有效thread token，只有ERROR_NO_TOKEN时读取process token的TokenUser SID；caller SID字符串、approved_by、Origin和loopback均不是身份来源。任意同一有效Windows用户程序可调用受控本地bootstrap取得自己的短时capability；没有capability的curl拒绝，不等于同用户程序不能代表用户。

仅已验证的synthetic私有root可显式初始化：保护当前private root的DACL并原子创建local-operator目录。Windows DACL回读要求protected root、实际owner SID、唯一FullAccess用户ACE；release.db和交接文件也必须只有同user ACE，无Everyone/Admin组额外授予。失败不签发grant，非Windows拒绝。未创建Windows账户或采集其它账户token；当前用户实际SID/ACL的现场证据不能声称实测了第二个登录用户，也不能防管理员获取所有权或同用户恶意程序——这对应用户选择的信任边界。

capability和CSRF为各32随机字节；DB只存hash、owner SID、instance、1h到期。PrivateOperatorGrant的repr隐藏两值。本地launcher handoff只写owner-only直接文件，CLI不输出secret、不放query/fragment/log；grant文件由受控本地程序读取后重核ACL、SID和DB。bootstrap不增加一轮人工审核。没有启用浏览器HTTP会话；后继接真实HTTP时必须用服务端session、HttpOnly/SameSite cookie、CSRF以及精确Origin/Host/framing防护，不能直接拿这个CLI作普通网络身份端点。

## 同库终审

private_final_decision_store在同release.db增加sessions/candidates/nonces/decisions/private_domain_final_approvals/successor表，显式迁移不丢原COMMON行。

prepare仅接store-owned reservation_id，重读实际完整plan字节、reservation identity/budget generation、逐字段COMMON VERIFIED readback及append-only event、target state/item/attempt。FrozenReview由此构造，caller不能提交FrozenReview或原始批准receipt。private R1/R2字段明确为synthetic stored plan projections，不声明真实R1/R2 provenance已接。critical比较包含identity、mutation、targets顺序、revision、private_images/private_prices、COMMON readback和稳定预算generation。技术预约/consume正常推进不改预算指纹。

decide只接有效本机grant、server nonce和展示时的review digest。BEGIN IMMEDIATE里重核owner/session/CSRF/nonce/当前domain冻结对象，消费nonce、写唯一decision和domain approval一事务；nonce失败则整笔rollback。并发点击只得到同一持久decision；响应丢失/重启用原nonce得到同receipt，独立同用户capability对同冻结对象亦不创建第二批准。nonce300s有效，仅批准后响应恢复保留同receipt；当前session有效性仍独立核查。

domain approval属于此private consumer，不借旧ReleaseStore.approve_plan的caller名字赋权。fake successor必须exact封闭fake类型，从此同库approval+实际payload取授权对象，先落UNKNOWN再fake write，最后同批准读回恢复。未知无读回时不重发，已有成功回读复用，不再问用户。冻结关键变化或丢失/损坏回读阻断执行；技术失败保留批准。同user重新bootstrap不会清空旧批准。

## 未交付正式接线

本段没有真实COMMON来源producer、正式R1/R2和渠道target-domain projection、完整HTTP浏览器session、Task接续、真实writer或正式DB迁移。正式consumer后继必须沿同库表合同演进，拒绝synthetic记录授真实权，不建第二approval ledger、不用caller allow_real开关。非关键provenance/container变更的service-owned rebind另需实现，当前仅exact同冻结对象及正常技术状态可恢复。

COMMON synthetic预算采用本测试包明确的Offer/account计数模型。原真实policy的maximum_confirmed_writes_per_product=1没有证明一生累计或一次publication lineage；真实producer后继须查批准范围和原文出处，不能把本fixture的保守计数固化为用户永久要求，也不能自行清零旧Offer额度。

## Windows原始依据

- [GetTokenInformation](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-gettokeninformation)：TokenUser数据取自有效token，不是HTTP caller身份。
- [CreateDirectoryW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createdirectoryw)：创建时应用security descriptor；文件系统必须支持ACL。
- [GetNamedSecurityInfoW](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getnamedsecurityinfow)：回读owner/DACL。
- [SetNamedSecurityInfoW](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow)：对显式synthetic目录设置protected DACL并传播继承；绝不操作正式数据目录。

这些API事实来自Microsoft原文，grant、nonce、SQL事务和private界面均为本段具体实现设计。当前没有HTTP.sys或浏览器认证声明。
