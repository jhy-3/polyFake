# 阶段一：Polymarket 架构与链上数据解码
## 学习目标
了解 Polymarket 的核心数据模型，包括 **事件**** (Event)**、**市场**** (Market)**、**条件**** (Condition)**、**集合**** (Collection)**、**头寸**** (Position ****或**** TokenId)**，并能用自己的话解释它们之间的关系。
理解 Polymarket 链上日志在“市场创建 → 交易 → 结算”全过程中的作用，以及不同日志之间如何串联形成**证据链**。
掌握**链上日志解析**的方法，能够实现：
**交易解码**** (Trade Decoder)**：给定交易哈希，解析链上交易日志，还原交易详情（价格、数量、方向等）。
**市场解码**** (Market Decoder)**：给定市场的 conditionId 或创建日志，还原该市场的链上参数（问题描述对应的标识、预言机地址、质押品、Yes/No 头寸 TokenId 等）。
## 核心概念
### 事件 (Event) 与市场 (Market)
在 Polymarket 中，**事件**代表一个预测主题，例如“某次美联储利率决议”。一个事件下可以包含一个或多个**市场**。每个市场对应该事件下的一个具体预测问题。例如，对于事件“2024 年美国大选”可以有多个市场：“候选人 A 当选总统？”、“候选人 B 当选总统？”等，每个市场通常是一个二元预测（Yes/No）。
**Market（市场）**：对应具体的 Yes/No 问题，是交易发生的基本单位。一些事件只有一个市场（例如简单的二元事件），而有些事件包含多个市场形成一个**多结果事件**。后者通常采用 Polymarket 的“负风险 (Negative Risk)”机制来提高资金效率（见下文）。
**Negative ****Risk（负风险）**：当一个事件包含多个互斥市场（即“赢家通吃”的多选一事件）时，Polymarket 引入 **NegRiskAdapter** 合约，将这些市场关联起来，提高流动性利用率。具体来说，在同一事件下，一个市场的 **NO** 头寸可以转换为该事件中**所有其他市场的**** YES** 头寸。这意味着持有任意一个结果的反向（NO）仓位，相当于持有对其他所有可能结果的正向（YES）仓位。通过这种机制，参与者不需要为每个可能结果都分别提供独立的资金，从而**提高资本效率**。NegRiskAdapter 合约提供了 convert 功能，实现 NO→YES 的头寸转换。
**示例**：假设事件是“谁将赢得选举？”，包含5个候选人作为5个市场（每个市场问“候选人X会赢吗？”）。在负风险架构下： - **YES** 代币表示下注某候选人获胜；**NO** 代币表示下注该候选人不赢。 - 如果最终候选人 A 赢了，那么持有 A 市场 **YES** 代币的人可以兑回 1 USDC；持有其他所有候选人市场 **NO** 代币的人也可以各兑回 1 USDC（因为那些候选人没赢）。相反，持有 A 的 **NO** 代币、以及其他候选人的 **YES** 代币都变得一文不值。 - 通过 NegRiskAdapter，可以将对某候选人的 NO 头寸随时转换为对其他所有候选人的 YES 头寸持有。这体现了所有候选人“不赢”的头寸和其他候选人“赢”的头寸是等价的，从而联通了各市场的流动性。
### 条件 (Condition)、问题 (Question)、集合 (Collection) 与头寸 (Position/TokenId)
Polymarket 使用 Gnosis 开发的 **条件代币框架**** (Conditional Token Framework, CTF)** 来实现预测市场的头寸代币化。在该框架下：
**Condition（条件）**：每个市场在链上的“登记身份”。创建市场时，会调用 CTF 合约的 prepareCondition 方法注册一个条件。**ConditionId** 是通过哈希计算得出的唯一标识：conditionId = keccak256(oracle, questionId, outcomeSlotCount)。其中：
oracle 是预言机合约地址（Polymarket 目前使用 UMA Optimistic Oracle 作为预言机）。
questionId 是问题的标识符（通常由问题内容等信息哈希得到，或 UMA Oracle 的 ancillary data 哈希）。
outcomeSlotCount 是结果选项数量。对于二元市场，该值为2。
Condition 就像市场的问题在链上的“出生证明”，绑定了唯一的问题ID和预言机。当市场需要结算时，预言机会针对这个 conditionId 发布结果。
**Position（头寸）**：头寸指的是用户持有的某市场某结果的份额（又称 **Outcome Share**）。Polymarket 将每个头寸实现为一个 **ERC-1155** 标准的可交易代币（又称 **PositionId** 或 **TokenId**）。每种结果对应一个不同的 TokenId，用于区分 YES 和 NO 两种头寸。
**CollectionId（集合ID）**：在条件代币框架中，中间引入了集合的概念，用于表示特定条件下某个**结果集合**。计算方法为：collectionId = keccak256(parentCollectionId, conditionId, indexSet)。其中 parentCollectionId 对于独立的条件通常为 bytes32(0)（Polymarket 所有市场都是独立条件，没有嵌套条件，因此 parentCollectionId 一律为 0）。indexSet 是一个二进制位掩码，表示选取哪些结果槽位。对于二元市场，有两个可能的 indexSet：
YES 头寸的 indexSet = 1 (0b01，表示选取第一个结果槽)。
NO 头寸的 indexSet = 2 (0b10，表示选取第二个结果槽)。
**TokenId（PositionId）**：最后，用抵押品代币地址和集合ID一起计算得到 ERC-1155 的 Token ID：tokenId = keccak256(collateralToken, collectionId)。在 Polymarket 中，**对于每个条件**** ****(市场)，会产生两个**** TokenId** —— 一个对应 YES 份额，一个对应 NO 份额。这两个 TokenId 是在该市场上交易的标的资产，代表了对同一预测问题的两种相反结果的头寸。
**Collateral（抵押品）**：Polymarket 市场的押注资金均以稳定币 USDC (Polygon 上为 USDC.e，地址 0x2791...Aa84174) 作为抵押品。每份 Outcome Token 背后对应 1 USDC 的抵押，当市场结算时兑现。**价格含义**：比如价格 0.60 USDC 意味着花 0.60 USDC 可购买该市场 1 份 YES 代币。如果该结果最终发生，持有者可赎回 1 USDC（获得净盈利 0.40 USDC）；如果未发生，则该代币价值归零，损失全部本金 0.60 USDC。因此，二元期权代币价格可以理解为市场对该事件发生概率的定价。
### 市场的完整生命周期与链上证据链
Polymarket 的市场从创建到结算，关键的链上步骤和日志事件如下：
**市场创建**** (Creation)** – *登记问题*：由市场创建者调用 ConditionalTokens.prepareCondition 创建条件。
**关键日志**：ConditionPreparation 事件，包含 conditionId、oracle、questionId、outcomeSlotCount 等信息。这个事件在链上确认了某预言机地址与问题ID的绑定关系，相当于市场的建立。一旦发布，预言机（UMA Optimistic Oracle）稍后将根据这个 conditionId 报告结果。
**初始流动性提供与拆分**** (Split)** – *生成初始头寸代币*：市场创建后，需要流动性提供者拆分出初始的 YES/NO 代币。通常通过调用 ConditionalTokens.splitPosition 将抵押品 USDC 拆分成等价值的 YES 和 NO 头寸。
**关键日志**：PositionSplit 事件，包含 conditionId、collateralToken（应为 USDC 地址）、parentCollectionId（一般为0）、partition（拆分出的 indexSets 列表，如 [1,2]）、以及 amount（拆分抵押品数量）。该事件证明抵押品被锁定，并生成了对应数量的 YES 和 NO 代币。对于二元市场，拆分 1 USDC 通常会得到面值各 1 USDC 的 YES 和 NO 代币各一枚。最初的流动性提供者可能是做市商，他们将USDC拆分为两种头寸代币，并可以在订单簿上挂单提供买卖报价。
**交易**** (Trading)** – *撮合买卖订单*：交易在 Polymarket 的链上撮合引擎（CLOB 合约）中进行。Polymarket 采用中心限价订单簿模型，订单撮合通过智能合约（对于普通二元市场是 **CTF Exchange**，多结果市场则通过 **NegRisk_CTFExchange**）完成。每笔撮合成交在链上记录交易日志：
**关键日志**：OrderFilled 事件。每当买卖双方的订单在链上部分或全部成交时，都会触发该事件，记录交易的详情，包括：
maker 和 taker 地址：做市（挂单）方和吃单方地址。
makerAssetId 和 takerAssetId：成交时双方各自支付的资产 ID（Polymarket 将资产用一个ID表示：**0 ****表示**** USDC**，非零则表示特定市场的头寸 TokenId）。
makerAmountFilled 和 takerAmountFilled：各方成交的数量（对应各自资产的数量，整数形式）。
fee：maker方支付的手续费数量。
因 Polymarket 交易总是发生在 USDC 和某个 Outcome Token 之间，所以一个 OrderFilled 事件里必然有一个资产是 USDC（资产ID为0），另一个资产是某市场的 TokenId。例如，若 makerAssetId = 0 且 takerAssetId 为某 TokenId，则表示**挂单方卖出**** ****USDC、买入了该**** TokenId**（即挂单方下的是买入该头寸的订单）。相应地，成交价可以通过 makerAmountFilled 和 takerAmountFilled 计算得出（需结合资产的精度，详见下文“交易日志解码”部分）。在订单完全撮合时，通常还会伴随一个 OrdersMatched 事件，它将多个 OrderFilled 事件归组表示一次撮合完成，但初学分析时可以主要关注 OrderFilled 日志。
值得注意的是，在 Polymarket 内部，真正的代币铸造和销毁与交易匹配是紧密相关的。当两个相反方向的订单成交且共同投入的 USDC 满足1:1配比时，会触发抵押品锁定和头寸代币铸造的过程。例如： - 如果一名买家愿意以0.70 USDC价格买入YES，另一名卖家（或另一买单的对手）愿意以0.30 USDC价格买入NO，两人的意向可匹配为一笔交易：总共1.00 USDC 被锁定，铸造出1个YES和1个NO代币，分别分配给出价0.70的买家和出价0.30的买家。这对应链上 PositionSplit 事件记录了1 USDC 拆分出一对头寸，以及后续的 OrderFilled 记录了双方各得到代币和支出USDC的情况。 - 类似地，如果一方想卖出YES代币，另一方想卖出相同市场的NO代币，两笔sell单可以匹配成一个“合并”操作：这两枚YES和NO代币被同时销毁并赎回总计1 USDC 给卖出方（各得相应报价的USDC）。这种情况下会出现 PositionsMerge 和 OrderFilled 等日志，表示头寸被合并赎回。这是 Polymarket 允许无需等待事件结算就能退出仓位的一种机制。
**结算**** (Resolution)** – *确定结果并清算*：当事件结果揭晓且到达市场设定的关闭时间后，预言机合约（例如 UMA Optimistic Oracle）将把结果提交回 CTF 合约，调用 reportPayouts(conditionId, payouts[]) 来公布各结果的兑付率。
**结果日志**：调用 reportPayouts 本身通常不会有特殊事件（或有 ConditionResolution 事件），但其效果是将相应 conditionId 下的头寸标记为可赎回：胜出的头寸代币每份价值 1 USDC，失败的头寸代币价值 0。用户随后可以调用 ConditionalTokens.redeemPositions 来赎回胜出代币的抵押品。Polymarket 当前使用 UMA 的乐观预言机机制，这意味着通常在预言机确认结果后，通过 Polymarket 前端或合约即可触发结算。结算完成后，对应的 YES/NO 代币可以兑换回 USDC，市场生命周期结束。
以上链上事件共同构成了市场的**证据链**：从 ConditionPreparation 证明市场的存在和参数、PositionSplit 证明资金注入和代币铸造、OrderFilled 记录交易交换细节、直到 reportPayouts 确认结果以供赎回。这些事件串联起来，可以让我们基于链上数据重建出市场发生的一切。
## 任务 A：交易日志解码 (Trade Decoder)
**问题描述**：实现一个通用的交易日志解析器，输入交易哈希（在 Polygon 链上），输出该交易中 Polymarket 订单撮合的详情。例如给定样本交易哈希 0xfa0746b1...9198（假设其中包含一个 OrderFilled 事件），需要解析得到如下 JSON 结构：
{
  "txHash": "0xfa0746b1...9198",
  "logIndex": 123,
  "exchange": "0xC5d5...f80a",        // 撮合发生的交易所合约地址
  "maker": "0x....",                  // 挂单方地址
  "taker": "0x....",                  // 吃单方地址
  "makerAssetId": "12345...",         // maker给出的资产ID
  "takerAssetId": "67890...",         // taker给出的资产ID
  "makerAmountFilled": "1000000",     // maker支付的资产数量（整数，可能需要转化单位）
  "takerAmountFilled": "500000",      // taker支付的资产数量
  "price": "0.5",                     // 成交价格（计算得到，单位USDC，例如0.5表示每份头寸0.5 USDC）
  "tokenId": "67890...",              // 本次交易涉及的Outcome Token的ID（非USDC的资产ID）
  "side": "BUY"                       // 表示这笔交易对该Outcome Token来说是买单成交（BUY）还是卖单成交（SELL）
}
**解析思路**：
**获取交易日志**：通过 Polygon RPC（如 eth_getTransactionReceipt）获取指定交易的所有日志。过滤出 OrderFilled 事件（根据其主题 topic 或合约地址匹配 Polymarket 交易所合约）。Polymarket 有两类撮合合约：普通二元市场使用 CTF Exchange，地址 0x4bFb41...8B8982E，多结果负风险市场使用 NegRisk_CTFExchange，地址 0xC5d563...5220f80a。这两合约的 OrderFilled 事件格式相同。
**解析字段含义**：按照 Polymarket 交易所合约的定义，提取 OrderFilled 日志中的各字段：
**makerAssetId / takerAssetId**：判断资产类型。Polymarket 约定资产 ID 为0表示 USDC（稳定币）；非零资产ID则对应某市场的头寸 TokenID。每次成交总是一方支付USDC，另一方支付头寸代币。可以通过比较 AssetId 是否为0，识别哪一方在支付 USDC、哪一方在支付 Outcome Token。
**makerAmountFilled / takerAmountFilled**：提取成交数量。需要注意不同资产的数量单位：USDC 在链上是 6 位小数精度，头寸代币（ERC-1155）本身不固定精度但通常可以视作与抵押品等值的最小单位（Polymarket 习惯上将数值按 USDC 的最小单位计算交易量）。因此，在计算价格时通常先将这两个数量归一化为实际数量（除以 10^6）。
**price（成交价）**：可通过成交的 USDC 数量除以成交的头寸代币数量得到。为确保小数精度正确，应使用实数计算：price = (USDC_filled / 1e6) / (token_filled / 1e6)。简化后实际上就是 price = USDC_filled / token_filled（因为双方均以最小单位计数）。例如，上例中 makerAmountFilled = 1000000 表示挂单方出了 1.000000 USDC，takerAmountFilled = 500000 表示吃单方出了 0.500000 份头寸（很可能对应0.5份YES代币，如果1份代表1 USDC的名义）。则 price = 1.0 / 0.5 = 2.0 USDC？（这里数字只是示例，真实情况下一份头寸通常以1 USDC为单位价值，所以0.5份头寸对应0.5 USDC价值，price 应为 0.5/0.5=1.0；示例给的数据似乎假设另一种量级，下文会给出合理案例）。
**tokenId**：确定哪个资产ID是 Outcome Token 的ID。通常我们可以简单判断：哪个 assetId 非0，就代表具体市场的头寸代币ID，即 tokenId 字段应填入非零的那个。在极端情况下，如果出现两侧 assetId 都非0（例如复杂多撮合事件），那可能是一次多市场联动，但Polymarket的OrderFilled单笔记录通常不出现两个非零，因为USDC必须参与交易。
**side（买卖方向）**：表示这笔成交相对于该 Token 的买卖方向。为了简化，可按以下规则判断：
如果 makerAssetId = 0（挂单方出USDC），意味着挂单方在**买入**头寸，而吃单方卖出头寸。因此整个成交对于该头寸来说是一次买单成交，我们可以将 side 记为 "BUY"。
如果 takerAssetId = 0（吃单方出USDC，挂单方出头寸），则表示有人买入USDC、卖出头寸，对该头寸来说这是一次卖单成交，标记为 "SELL"。
Polymarket 官方文档也指出：makerAssetId 为0意味着订单类型是 BUY（用 USDC 换取结果代币）；takerAssetId 为0意味着订单类型是 SELL（得到 USDC，卖出结果代币）。因此上述判定与文档定义一致。
**组装**** JSON ****输出**：将提取的信息格式化为所需的 JSON。txHash 为交易哈希本身，logIndex 为日志在交易中的索引（确保唯一定位一条成交记录），exchange 为发生交易的合约地址（可以从日志的 address 字段获取），其余字段根据解析结果填入。**注意**：要保证 price 等数值是友好的可读格式（通常用字符串表示数值即可避免精度问题），side 用大写字符串 "BUY" 或 "SELL" 表示。
**示例**：假设从交易日志解析得知： - makerAssetId = 0（maker出USDC），takerAssetId = 0x1234... (某TokenId) - makerAmountFilled = 3000000 （表示3.000000 USDC） - takerAmountFilled = 3000000 （表示3.000000份头寸代币；这里可能表示3份YES或NO代币） - 则 tokenId = 0x1234...，price = (3.000000 USDC) / (3.000000 token) = 1.0 USDC。 - 根据 makerAssetId=0，这笔成交对该 Token 来说是 BUY 方向。
输出的 JSON 类似：
{
  "txHash": "0xabc123...def",
  "logIndex": 2,
  "exchange": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
  "maker": "0xMakerAddr...",
  "taker": "0xTakerAddr...",
  "makerAssetId": "0",
  "takerAssetId": "0x1234...5678",
  "makerAmountFilled": "3000000",
  "takerAmountFilled": "3000000",
  "price": "1.0",
  "tokenId": "0x1234...5678",
  "side": "BUY"
}
这个结果表明：在交易哈希为0xabc123的订单中，挂单方用3 USDC 买入了3份 TokenId为0x1234...5678的头寸，每份价格为1.0 USDC。
**注意**：真实链上可能出现更复杂的成交情况，例如一次撮合涉及多条 OrderFilled 日志（大订单被多笔小订单填充），或者在负风险市场中一次撮合转化多个头寸。但在初步实现解码器时，我们可以先处理简单场景——即每条 OrderFilled 独立解析。如果需要处理更复杂的情况，可以根据 OrdersMatched 或结合多条日志进一步整合信息。
## 任务 B：市场参数解码 (Market Decoder)
**问题描述**：给定链上获取的市场创建相关信息（如 ConditionPreparation 日志，或已知的 conditionId），提取并计算出该市场的核心链上参数，包括预言机、问题ID、抵押品地址，以及 YES/NO 两种头寸的 TokenId。
**输入可能**： - 直接提供 conditionId 值，或 - 一个 ConditionPreparation 事件日志记录（其中包含了创建时的 oracle 地址、questionId 等）。
另外，Polymarket 还有 Gamma API 提供的市场元数据，例如通过 **slug**（市场短标识）查询获取市场信息，其中可能包含 conditionId 和头寸 tokenId 列表等。如果使用外部 API，需要校验其数据与链上计算结果一致。
**输出要求**：JSON 结构例如：
{
  "conditionId": "0xabc...123",
  "questionId": "0xdef...456",
  "oracle": "0xOracleAddr...789",
  "collateralToken": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  // USDC.e 地址
  "yesTokenId": "0xYesTokenId...",
  "noTokenId": "0xNoTokenId..."
}
其中 yesTokenId 和 noTokenId 是该市场的 YES/NO 头寸在链上的 ERC-1155 代币ID。
**解码步骤**：
**获取原始参数**：如果输入的是日志，直接从 ConditionPreparation 事件中读取：
conditionId：日志的主题或数据字段之一。
oracle：预言机合约地址。
questionId：问题标识（bytes32）。
outcomeSlotCount：结果数量（应为2）。 如果输入只有 conditionId，则需要从上下文或其他渠道获得对应的 oracle 和 questionId；通常 Gamma API 的市场数据中会有 questionId 和 oracle 名称等信息以供参考。 抵押品 collateralToken 在 Polymarket 中几乎总是 USDC.e 的地址，可视作已知常量（或通过 PositionSplit 日志交叉验证：该日志通常包含 collateralToken 字段）。
**计算**** TokenID**：按照 Gnosis 条件代币框架计算头寸 token 的ID：
**CollectionId**：先计算针对 YES 和 NO 的集合ID。使用框架提供的方法或公式：
collectionId_yes = keccak256(parentCollectionId, conditionId, 1)，其中 parentCollectionId = bytes32(0)，1表示 indexSet=0b01 对应第一个结果槽。
collectionId_no = keccak256(parentCollectionId, conditionId, 2)，其中 2表示 indexSet=0b10 对应第二个结果槽。 Polymarket 官方也有相同定义。这一步得到一对 bytes32 的 collectionId，对应该市场的两个互斥结果。
**PositionId (TokenId)**：再将抵押品代币地址与上述 collectionId 结合哈希：
yesTokenId = keccak256(collateralToken, collectionId_yes)。
noTokenId = keccak256(collateralToken, collectionId_no)。 这样就得到了该市场 YES 和 NO 份额的 TokenId。因为 collateralToken 对于Polymarket当前固定为 USDC.e，所以某种意义上TokenId由 conditionId 和 indexSet 唯一决定。 （在实现中，可直接调用已部署的 ConditionalTokens 合约的 getPositionId 方法，或使用 solidity/python 等方式手动计算哈希值。Polymarket 文档明确给出了计算步骤和顺序。）
**组装输出并验证**：将得到的参数填入 JSON。为了验证正确性，可以交叉检查：
计算所得 yesTokenId/noTokenId 是否和 Gamma API 返回的 clobTokenIds 匹配（如果有使用 Gamma 数据）。Polymarket Gamma API 的 Market 信息通常会列出这个市场对应的两个 tokenId，以供前端下单等使用。我们的计算结果应与之完全一致，否则可能说明输入有误或我们计算有误。
如果事先解析过该市场的交易（任务 A），可以检查某笔交易解析出的 tokenId 是否等于这里算出的 yes/no TokenId 之一。如果匹配上了，说明这个交易确实属于该市场。例如，若 Trade Decoder 输出的一笔交易 tokenId = 0x...89，而 Market Decoder 计算的 yesTokenId = 0x...89，则可以确认这笔交易是在买卖该市场的 YES 份额。
通过 Market Decoder，我们能从链上**推断出市场的基础结构**（问题、预言机、抵押品、头寸资产ID等）。配合 Trade Decoder，可以进一步将**链上交易记录归类到各个市场**中，奠定索引器开发的基础。
### 数据示例
假设通过 Gamma API 查询到 slug 为 "fed-rate-jan2024" 的市场信息，得到了 conditionId = 0xabc...123，Oracle 为 UMAAdapterV2 地址 0xUMA...oracle, questionId = 0xdef...456。我们知道 collateralToken = USDC.e (0x2791...Aa84174)。按照上述步骤计算： - collectionId_yes = keccak256(0x0, 0xabc...123, 1) - collectionId_no = keccak256(0x0, 0xabc...123, 2) - yesTokenId = keccak256(0x2791...Aa84174, collectionId_yes) - noTokenId = keccak256(0x2791...Aa84174, collectionId_no)
最终输出：
{
  "conditionId": "0xabc...123",
  "questionId": "0xdef...456",
  "oracle": "0xUMA...oracle",
  "collateralToken": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
  "yesTokenId": "0xAAA...111",
  "noTokenId": "0xBBB...222"
}
并可验证 Trade Decoder 获取的所有交易 tokenId 在这两个值之中。
### 注意事项
**精度与单位**：在计算价格或组装输出时，要注意 USDC 的精度是6位（小数点后6位）。Polymarket 头寸代币由于基于抵押品拆分，通常也是按USDC的基础单位发行（1份代币=1 USDC抵押）。很多链上数据（如 makerAmountFilled）是整数形式，需要除以 1e6 转成人类可读的数量。
**方向判定**：我们的 Trade Decoder 里简单用 makerAssetId 是否为0来判定买卖。如果需要更精细，也可以结合 maker 或 taker 是不是用户自身来决定视角（但一般而言，用资产类别判断交易方向就足够，如上文规则）。
**复杂订单**：负风险市场中，可能出现单笔交易一个 OrderFilled 订单撮合同时涉及多个市场的情形（通过 NO 到 YES 的转换）。这种情况下会出现特殊的 PositionsConverted 事件。基础版本的解码器可以暂不处理此类复杂日志，但在设计上应考虑留意同一交易hash中是否存在多个 OrderFilled 以及 PositionsConverted，以免误分类交易归属。
### 数据固化 (Fixtures)
在开发和测试过程中，建议将上述解析得到的关键数据保存为 JSON 文件，方便离线测试。例如： - 将 Polygon 上抓取的交易回执 (包含日志的 transaction receipt) 保存为 fixtures/tx_<hash>.json。 - 将 Gamma API 获取的市场信息结果保存为 fixtures/market_<slug>.json。 这样做可以确保在没有链上连接时也能进行解析函数的单元测试，并保证解析逻辑的幂等和正确。

# 阶段二：Polymarket 链上市场与交易索引器
## 学习目标
通过实战构建一个针对 Polymarket 市场和交易的链上数据索引器，理解从**链上原始数据**到**业务语义层数据**对齐的完整流程。具体目标： - 理解链上日志数据如何与 Polymarket 应用层的市场概念对应，并设计流程定期同步链上数据。 - 实现一个最小可用的索引服务，能够扫描 Polygon 上 Polymarket 合约的交易数据，将市场和交易信息存储到本地数据库，并提供查询接口。 - 培养工程上的健壮性考虑，包括数据一致性校验、错误重试、断点续传、幂等写入等机制，确保索引器长期稳定运行。
## 系统架构概览
一个 Polymarket 数据索引系统大体上由**数据源**、**处理管道**、和**数据库/接口**三部分组成。
### 数据源 (Data Sources)
**链上数据**** (Primary)**：主要来源是区块链节点的 RPC 接口或区块链事件订阅。我们需要从 Polygon 链上获取与 Polymarket 相关的合约事件，例如 OrderFilled, OrdersMatched（来自交易撮合合约），ConditionPreparation, PositionSplit, PositionsMerge, PayoutRedemption 等（来自 ConditionalTokens 或适配器合约）。这些链上事件是**权威的交易和状态记录**，构成最终事实依据。
**链下数据**** (Secondary)**：Polymarket 提供的 Gamma API。这属于中心化服务，提供了市场的元数据（例如市场的 slug、标题描述、分类标签、是否负风险、状态等）和某些链上参数的缓存（如 conditionId、questionId、clobTokenIds 等）。虽然这些数据在链上有对应来源，但通过 API 获取可以简化解析过程，并提供丰富的语义信息（例如市场问题的文本描述、截止时间等）。**Secondary ****数据用于补充说明和验证**，但应与 Primary 数据交叉核对，以确保一致。
### 索引流程 (Pipeline)
索引器需要持续地发现新市场、同步交易，并提供查询服务。可以将流程分解为几个阶段：
**市场发现**** (Market Discovery)**：定期或在启动时，从 Gamma API 拉取**事件列表**和**市场列表**。由于 Gamma 将 Market 组织在 Event 下，我们可以选取感兴趣的 Event（例如通过 slug 明确指定）或者通过 Gamma 的 /markets 列表端点获取所有当前活动的市场。对于每个 Market 数据，提取其中的关键信息：
slug：市场短标识符，用于友好地标记市场。
conditionId：链上条件ID。
questionId：链上问题ID（对应 UMA Oracle 的问题哈希）。
oracle：预言机合约地址（通常 UMA Adapter 合约地址）。
enableOrderBook / status 等：指示该市场是否开启交易，以及市场状态（未结算/已结算等）。
clobTokenIds：这通常是一个包含两个 tokenId 的列表，分别对应市场的 Yes 和 No 头寸。在 Gamma API 文档中，提到 Market 映射到“一对 CLOB token ids”。
**验证**：对于拿到的 conditionId 和 clobTokenIds，可以本地计算一遍（参考阶段一任务 B 中 Market Decoder）以确保 Gamma 数据可靠。即使用 conditionId 和 collateral USDC 地址计算 yes/no TokenId，核对是否和 clobTokenIds吻合。如果不符，需谨慎处理（可能是数据错误或版本差异）。
将新的市场记录插入数据库的 markets 表（结构见下文），或更新已有市场的信息（例如市场状态改变）。
**历史数据同步**** (Backfill/Sync)**：针对数据库中已登记的市场，抓取其历史和实时交易事件。实现上可以有两种策略：
**按区块高度顺序全局扫描**：如果 Polymarket 的交易主要发生在固定的几个合约（如前述两个 Exchange 合约地址），我们可以针对这些合约地址，用 eth_getLogs 按区块范围批量获取日志。例如每次获取Exchange合约在某区块范围内的所有 OrderFilled 事件。这样效率较高且实现简单。由于 Polymarket 两个主要撮合合约地址已知，我们可以通过 topics 过滤指定 OrderFilled 事件的签名和这些地址来抓取所有成交记录。
**按市场逐个扫描**：(不太必要) 也可依据每个市场的 tokenId 过滤 Transfer 或 PositionSplit，但Polymarket没有单独的市场合约，所以一般不这么做。而是直接扫描交易所合约的事件即可拿到全部交易，再按 tokenId 分类归属市场。
实际工程中，会设置一个起始区块 fromBlock（比如 Polymarket上线Polygon的开始区块或上次中断的区块），以及一个每次处理的 toBlock 批次范围。循环调用 getLogs 获取这些区块间的所有相关事件日志。**需要考虑限流和响应大小**：可以按比如 10000 区块为一批，逐段扫描。同时，维护一个全局的**同步状态**（如 sync_state 表或本地文件）记录当前已处理到的最新区块高度，以支持断点续传。
**解码与处理**** (Decoding & Processing)**：对于获取到的 OrderFilled 日志列表，逐条解析（利用阶段一编写的 Trade Decoder）。解析出交易详情后，需要将其**归类到对应市场**：
日志中的 makerAssetId 或 takerAssetId 非零值，就是交易涉及的头寸 TokenId（例如 tokenId = 0x...1234）。通过查找数据库 markets 表，找到记录中 yes_token_id 或 no_token_id 等于该 TokenId 的市场。这样即可确定此交易属于哪个市场。
获取该市场的主键 market_id，连同交易信息一起准备写入 trades 表。
同时可以确定交易的具体 **outcome**（是 Yes 还是 No）。例如如果匹配的是 yes_token_id，可以在需要时标注这笔交易在买卖 "YES" 头寸；匹配 no_token_id 则是针对 "NO" 头寸。（在基本要求中，可以不特别标明，但在扩展应用中可能有用，比如区分买卖的到底是哪一边结果）。
注意处理**重复数据**：由于链上日志唯一由 (tx_hash, log_index) 标识，我们可以在插入数据库时设置该组合为唯一键，避免重复插入同一条链上记录。解析过程中也可以自行去重（比如用集合暂存已处理过的 tx+index）。
此外，需要处理其他可能的日志类型： - PositionsSplit / PositionsMerge：虽然我们的重点是交易，但这些日志可以帮助确认市场创建和销毁情况。尤其 PositionSplit 可以作为市场存在和初始流动的证据，可选地记录下来。 - OrdersMatched：此事件主要将一系列 OrderFilled 关联起来（提供撮合订单哈希等信息），一般可用于统计撮合次数，但对成交列表本身影响不大，可暂时忽略或者简单记录。 - 如果考虑负风险市场的转换，还可能有 PositionsConverted 事件。初期索引器可以不深入解析转换事件，只要记录有交易发生即可。
**存储**** (Storage)**：将解析好的交易数据批量写入数据库中的 trades 表，以及相应更新市场状态：
对每条交易，插入一行包含交易哈希、市场ID、价格、数量、方向、时间戳等信息的记录。
时间戳可以通过交易所在区块的时间获得（可从区块缓存或通过 RPC 获取区块信息)。有的日志提供 blockTimestamp（如果 RPC支持 eth_getLogs 返回），否则需要额外查询区块。
市场表可能也需要更新一些信息，比如最后交易时间、累计交易量等，可在此阶段一起完成（或由查询时计算）。
更新 sync_state 存储新的 last_block，高度至少到 toBlock，下次扫描从那里开始。这样即使程序中断，也能从上次中止处继续，不会漏掉或重复。
整个流程运行后，就建立了链上数据和本地语义数据的索引映射：每个市场知道了自己的 tokenId，预言机等信息，交易表记录了所有和这些市场相关的成交历史。这为后续提供查询和分析服务奠定了基础。
### 数据库设计 (Schema)
为了存储上述数据，我们需要设计关系型表结构来高效查询。根据任务描述，建议的表结构如下：
**markets**** ****表** – 市场基本信息，每条记录对应 Polymarket 的一个市场（通常一个问题的 Yes/No预测）：
（实际实现中，字符串长度可根据需要调整为定长或 TEXT。）
**trades**** ****表** – 交易记录，每条记录对应链上一笔 OrderFilled 事件（可能是部分成交，也可能是完整撮合的一部分）：
**唯一索引**：(tx_hash, log_index) 以确保重复插入时违反唯一性，从而实现幂等。
**sync_state**** ****表** – 存储同步进度等信息：
可以将 key 设成 'market_sync' 和 'trade_sync' 分别跟踪市场和交易的同步进度，或者简单用 'global_indexer' 一个key记录整体同步进度。
## 任务拆解
### 任务 A: Market Discovery Service 实现
需求：编写一个脚本或服务，负责定期发现新的市场并存储市场信息。
**实现步骤**： 1. **获取市场列表**：利用 Polymarket Gamma API 提供的接口获取市场数据。Gamma 提供了按事件获取市场和直接获取所有市场等方式。例如，可以调用 GET /markets 或基于给定的 Event slug 调用 GET /events/{slug}/markets。假设我们有事件 slug，如 "fed-decision-in-january"，就获取该事件下的所有市场列表。 2. **解析市场数据**：对于每个市场条目，提取我们关心的信息： - slug（有的市场Slug可能和事件slug相关或带序号，如 fed-rate-jan2024-unchanged 之类）。 - conditionId, questionId, oracleAddress, outcomeSlotCount 等链上参数。 - collateralToken（一般应为USDC地址，可默认为USDC，如果API未提供就填默认值）。 - clobTokenIds 列表（大小为2的数组，对应 Yes/No tokenId）。有些API输出可能标记为 token0/token1 或类似字段，要对照文档理解。 - 市场描述、状态（active/inactive/resolved）、截至时间等额外信息也可获取保存。 3. **校验数据**：用链上公式校验 clobTokenIds 是否正确： - 根据拿到的 conditionId，使用我们在阶段一编写的算法重新计算 yes/no TokenId，确保与 API 给出值吻合。如果不一致，需打印警告或暂不记录该市场，避免错误数据。 - 确认 collateralToken 是预期的 USDC 地址。 4. **存储到数据库**：将市场信息插入或更新到 markets 表： - 如果表中已存在相同的 conditionId（或 slug），则更新其信息（可能是状态变化或补充标题等）。 - 如不存在，则插入新记录，生成新的 id。 - 特别地，记录 enable_neg_risk（是否负风险事件，可从 Gamma的 event.negRisk 字段或市场数量判断：如果同一事件有多市场则为 true）。 - 记录 created_at（可取 Gamma 提供的创建时间，或通过链上 ConditionPreparation 的区块时间，但前者获取方便）。
**定期运行**：将上述流程设置为一个周期任务，每隔一段时间（如每小时）调用，以捕获新上线的市场。或者在启动时拉取一次全量市场。也可以根据 Gamma 提供的“最新市场ID”增量拉取。
通过 Market Discovery，我们确保索引器掌握最新的市场清单，为后续同步交易做准备。
### 任务 B: Trades Indexer 实现
需求：实现一个函数如 run_indexer(from_block, to_block)，扫描指定区块范围内 Polymarket 的交易日志，并将交易存入数据库。
**实现要点**： 1. **获取日志**：使用 RPC 的 eth_getLogs 接口，构造过滤参数： - address: 设置为 Polymarket Exchange 合约地址列表（可以两个地址都填上，以同时获取 Binary和NegRisk交易所的事件）。 - topics[0]: 设置为 OrderFilled 事件的事件签名哈希（可以在合约ABI中找到，或根据已知字段计算）。这将仅返回我们需要的成交事件。 - fromBlock 和 toBlock: 设置为函数参数，或者从上次同步位置读取。本次要处理的区块区间。注意不要一次跨度太大以免超时，根据需要拆分多次调用。 - 例：getLogs({ address: [exch1, exch2], topics: [ORDER_FILLED_TOPIC], fromBlock: 40000000, toBlock: 40010000 })。
**解析日志列表**：对返回的每条日志，应用 Trade Decoder（任务 A）提取结构化数据：
解出 tx_hash, log_index, maker, taker, makerAssetId, takerAssetId, amounts, price, tokenId, side 等。
通过 tokenId 找到所属市场的 market_id（查询数据库 markets 表）。如未找到匹配市场，可能意味着**出现了尚未记录的新市场**：
对于这种情况，可调用 Market Discovery 流程补充该市场（例如某些市场是在我们上次获取后新创建但Gamma未及时提供，或者我们的市场列表滞后）。这相当于**动态市场发现**：索引交易时发现未知 tokenId则立即去 Gamma API 获取它的市场信息并入库。
如果仍无法匹配，需记录异常日志以便调查。
确定 outcome 类型：若 tokenId 等于 market.yes_token_id，则 outcome = "YES"，若等于 no_token_id 则为 "NO"。
整理 price 和 size：price 已有，size 则取成交的头寸数量（记得换算为实际单位，一般 size = takerAmountFilled/1e6 *如果takerAssetId是头寸*, 或 makerAmountFilled/1e6 *如果makerAssetId是头寸*，总之就是Outcome Token的实际张数）。
时间戳：日志本身不含时间，需要查询该日志所在区块时间。可以在获取日志时顺便获取 blockNumber，然后批量查区块时间，或者利用本地缓存。也可以在事后通过交易哈希调用 eth_getTransactionReceipt 再取 blockNumber+再查时间。但为了效率，推荐批量区块查询。常用方法是：维护一个简单缓存字典，遇到新 blockNumber 用 eth_getBlockByNumber 查询一次时间戳，存入缓存。
**写入数据库**：采用批量插入或逐条插入 trades 表：
插入前可以先按照 (tx_hash, log_index) 排序数据，确保写入顺序一致（可选）。
利用数据库的唯一键防止重复插入。如果数据库支持 UPSERT，可以直接 UPSERT，否则捕获重复错误后忽略。
每个插入包括所有解析出的字段。Decimal 类型字段注意以合适类型写（或转为字符串后由SQL转换）。
建议在同一事务中完成一批区块范围的插入，确保**原子性**。
**更新同步点**：当该批次 toBlock 的日志处理完毕，更新 sync_state.last_block 为 toBlock 或 toBlock_processed。这样即使程序停止，下次启动知道从哪里继续。更新时也记录当前时间。
**循环运行**：可以将上述流程放在一个循环或调度中，不断向前推进块高，直至最新区块。同步初期可能需要从 Polymarket上线以来的早期区块开始 backfill，完成历史数据入库。之后进入实时同步模式，可每隔几秒/块查询一次新日志。
**注意**：使用 eth_getLogs 批量获取历史数据时，要小心**RPC提供商限频和数据量**。可以加上**指数退避重试**，以及对 fromBlock-toBlock 区间根据返回数据量动态调整大小。如果链上交易频繁，一个区间日志很多，可以缩短区间，以避免单次返回过大数据。Polymarket每日交易量相对适中，但遇到热点事件可能有大量成交，应考虑性能。
### 任务 C: 查询 API 服务
最后，为了提供方便的数据查询接口，我们可以构建一个简单的 REST API（使用 FastAPI、Flask 等皆可）来查询数据库内容。两个基础接口：
**GET ****/markets/{slug}**：输入市场 slug，返回该市场的详细信息（对应 markets 表的内容）。包括 conditionId, questionId, tokenIds, 以及市场的文本描述、状态等。示例返回：
{
  "slug": "fed-rate-jan2024-unchanged",
  "title": "美联储1月是否维持利率不变？",
  "conditionId": "0xabc...123",
  "oracle": "0xUMAAdapterV2...",
  "yesTokenId": "0xYYY...",
  "noTokenId": "0xZZZ...",
  "status": "active",
  "created_at": "2024-01-01T00:00:00Z",
  ...
}
如果找不到该 slug，返回 404 或空结果。
**GET ****/markets/{slug}/trades?limit=100&offset=0**：分页查询该市场的历史交易记录。可以通过 join trades 表获取数据，或在 trades 表直接筛选 market_id 来获取。返回 JSON 列表，每项包含交易详情（时间戳、价格、数量、买卖方等）。例如：
[
  {
    "timestamp": "2024-01-05T12:00:00Z",
    "side": "BUY",
    "outcome": "YES",
    "price": "0.45",
    "size": "100.0",
    "maker": "0xMaker...",
    "taker": "0xTaker..."
  },
  { ... },
  ...
]
列表默认按时间排序（可以按 tx_hash 顺序近似认为按时间，因为同区块内顺序可忽略）。可提供参数控制排序或过滤（比如按地址过滤该用户的交易）。
**实现提示**： - 使用一个轻量的 web 框架建立路由，对接数据库查询即可。对于频繁查询，可以添加简单的缓存。 - 注意对 slug 或输入做校验，防止SQL注入（如果直接拼接SQL的话）。 - 若性能要求高，可在 trades 表建立 market_id, timestamp 的索引，加速按市场查询排序。
通过这些 API，前端或用户就可以方便地按市场获取链上的交易数据，验证我们的索引结果。
## 工程实现关键点
**断点续传**：务必确保索引器可以随时中断和重启而不造成数据丢失或重复。sync_state 的 last_block 是核心。实现时，可以在启动时读取 last_block，从 last_block+1 开始继续扫描（因为 last_block 可能已处理完）。在处理过程中，先不立即更新 last_block，待确认该批次完整写入成功后再更新，以免中途失败导致进度丢失。对于市场列表，同样可以有类似机制（记录最后同步的市场ID等）。但由于 Gamma API 可以直接获取全量市场，简单起见每次都全量拉取更新也未尝不可。
**错误重试**：链上 RPC 有时会超时或失败。应对每次 getLogs 和 getBlock 请求做好异常处理和重试策略。例如，封装一个带有 **指数退避** (exponential backoff) 的重试，出现网络错误或服务端错误时等待一段时间再重试，最多重试N次。在批量同步初期，大量请求可能触发速率限制，合理安排请求节奏和并发。
**数据一致性**：由于我们结合链上和链下数据，必须注意两者的同步。**永远以链上数据为准**：即使 Gamma API 提供的市场列表可能有延迟或错误，我们也不能漏掉链上实际发生的交易。因此，才需要在解析交易时动态发现未知市场并补录。同时，当市场结算时，Gamma API 会更新其状态为 resolved，我们也应从链上检测到 ConditionResolution 或通过预言机事件知道市场结束，从而更新数据库状态。这两种来源可以互相验证。如出现冲突（链上显示结算但Gamma未更新或相反），应倾向相信链上并进行记录。
**幂等写入**：如前所述，插入数据库时利用唯一键避免重复。对于市场数据，也可用 conditionId 作为唯一键，后插入的相同conditionId更新已有记录而不是新增。幂等保证即使某段区块重复处理（可能因为重启时 last_block 没更新好，或者手工重新跑某范围），不会产生重复交易记录。
**性能和扩展**：随着 Polymarket 市场数量和交易量增长，注意数据库索引和查询效率。例如 trades 表按 market_id 查询需要索引支撑。批量插入可以提升写入性能。可以考虑使用异步方式处理 I/O（如使用 asyncio 或多线程）来同时获取链上数据和Gamma数据。进一步扩展可以引入消息队列，分离出获取和处理的阶段，但在本任务范围内简单顺序流程即可。
## 进阶思考
**链上重组**** (Reorg) ****处理**：在罕见情况下，Polygon区块链可能发生区块重组，使某些已确认的交易被替换或日志被回滚。如果索引器只按照区块高度推进，一旦遇到重组，可能会记录一些幽灵交易或漏掉重组后的新交易。一个稳健的方案是在同步最新数据时**延后几个区块确认**：比如当前区块高度是 N，索引器只处理到 N-5 或 N-10，然后等待。当检测到链上出现重组（可通过比较已处理区块的哈希变化），需要删除回滚的那部分数据并重跑。因此，维护每条交易的 blockNumber以及blockHash在数据库也有帮助，用于比对。当然，在Polygon上大规模reorg极少，但小的临时reorg是可能的，索引器应有相应设计。
**复杂交易拆解**：在负风险市场中，一个用户的下单可能触发更复杂的撮合场景。例如，一笔 Order 可能同时与多个对手单成交，或者通过 NegRiskAdapter 将一种头寸转换为多种头寸。链上体现为多条 OrderFilled 和 PositionsConverted 等事件。在我们的模型中，我们将每个 OrderFilled 都记录为独立交易记录。这对于统计成交量等已足够。但如果要还原更高层的意图（比如用户一次操作的整体情况），需要结合多条日志分析，这属于更高级的解析，不在当前索引范围内。初期索引器可以忽略这种拆解需求，只聚焦于逐笔链上成交记录的简单、完整记录。
综上，通过完成阶段二的索引器构建，我们能够将阶段一学到的 Polymarket 链上数据解析知识应用到实际工程中，搭建起从链上数据到业务数据库的桥梁。在确保准确性、一致性的基础上，索引器可以支持丰富的应用，比如实时行情、历史数据分析、用户盈亏计算等，为 Polymarket 生态提供重要的数据基础设施。

[1]   Gamma Structure - Polymarket Documentation
https://docs.polymarket.com/developers/gamma-markets-api/gamma-structure
   Overview - Polymarket Documentation
https://docs.polymarket.com/developers/neg-risk/overview
          Decoding the Digital Tea Leaves: A Guide to Analyzing Polymarket’s On-Chain Order Data - Zichao Yang
https://yzc.me/x01Crypto/decoding-polymarket
      Overview - Polymarket Documentation
https://docs.polymarket.com/developers/CTF/overview
 How Are Prediction Markets Resolved? - Polymarket Documentation
https://docs.polymarket.com/polymarket-learn/markets/how-are-markets-resolved
 Onchain Order Info - Polymarket Documentation
https://docs.polymarket.com/developers/CLOB/orders/onchain-order-info
       GitHub - warproxxx/poly_data: Polymarket Data Retriever that fetches, processes, and structures Polymarket data including markets, order events and trades.
https://github.com/warproxxx/poly_data
 Polymarket Data API - Bitquery Documentation
https://docs.bitquery.io/docs/examples/polymarket-api/
 Fetching Market Data - Polymarket Documentation
https://docs.polymarket.com/quickstart/fetching-data
 Historical Timeseries Data - Polymarket Documentation
https://docs.polymarket.com/developers/CLOB/timeseries
 How to Fetch Markets - Polymarket Documentation
https://docs.polymarket.com/developers/gamma-markets-api/fetch-markets-guide

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | INTEGER (PK) | 市场内部ID，自增主键 |
| slug | VARCHAR | 市场短标识（如 "fed-rate-jan2024"） |
| condition_id | VARCHAR(66) | 条件ID（链上 bytes32，0x 开头 64字符） |
| question_id | VARCHAR(66) | 问题ID（链上 bytes32） |
| oracle | VARCHAR(42) | 预言机合约地址（链上地址） |
| outcome_count | INTEGER | 结果数量（通常2） |
| enable_neg_risk | BOOLEAN | 是否为负风险事件（多市场事件） |
| yes_token_id | VARCHAR(66) | YES 头寸的 ERC1155 Token ID |
| no_token_id | VARCHAR(66) | NO 头寸的 ERC1155 Token ID |
| collateral_token | VARCHAR(42) | 抵押品合约地址（通常为 USDC.e 地址） |
| title | TEXT | 市场描述/标题（可选，从Gamma获取） |
| status | VARCHAR | 市场状态（如 active, resolved 等） |
| created_at | DATETIME | 市场创建时间（链上 ConditionPreparation 时间） |
| resolved_at | DATETIME | 市场结算时间（如果已结算） |


| 字段 | 类型 | 说明 |
| --- | --- | --- |
| tx_hash | VARCHAR(66) | 交易哈希（0x开头） |
| log_index | INTEGER | 日志索引（同一交易内唯一序号，用于定位事件） |
| market_id | INTEGER | 所属市场的 ID（外键引用 markets.id） |
| token_id | VARCHAR(66) | 交易涉及的头寸 TokenId（冗余存储，方便检查） |
| outcome | VARCHAR | 交易的预测方向（"YES" 或 "NO"，可选） |
| side | VARCHAR | 买卖方向（"BUY" 或 "SELL"） |
| price | DECIMAL(10,4) | 成交价格（USDC per share） |
| size | DECIMAL(18,6) | 成交数量（Outcome Token张数，精度按需） |
| maker | VARCHAR(42) | 挂单地址 |
| taker | VARCHAR(42) | 吃单地址 |
| timestamp | DATETIME | 成交时间（区块时间戳） |


| 字段 | 类型 | 说明 |
| --- | --- | --- |
| key | VARCHAR | 标识名称，例如 "polymarket_indexer" |
| last_block | INTEGER | 已同步的最后区块高度 |
| updated_at | DATETIME | 更新时间 |
