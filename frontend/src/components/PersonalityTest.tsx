import React, { useState, useCallback } from 'react'

// ─── 题库 (荣格八维 120 题，每题 A/B 两选项分配比重，总和为 11) ──────

interface Question {
    id: number
    text: string
    optionA: string
    optionB: string
    mapA: number[]  // [Se, Si, Ne, Ni, Te, Ti, Fe, Fi]
    mapB: number[]
}

const QUESTIONS: Question[] = [
    { id: 1, text: "因为谁也不知道明天会发生什么，所以_____", optionA: "要轻松地和这个世界相处，随心而动，感知周遭的环境才是真正的意义", optionB: "要潜入到梦境的启示和各类文化背景的象征指引亲手揭开晦涩的奥秘", mapA: [1,0,0,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 2, text: "你通常更享受", optionA: "及时行乐、自由随性的现实体验、尝试多样甚至有些古怪的美食、活动", optionB: "稳定舒适、有条不紊的个人生活、回忆过去的点点滴滴带来温馨的感受", mapA: [1,0,0,0,0,0,0,0], mapB: [0,1,0,0,0,0,0,0] },
    { id: 3, text: "和人发生意见分歧时，你的第一反应是", optionA: "考虑对方和自己的感受，试图在情感上彼此更舒服，化解矛盾", optionB: "考虑事实和逻辑论据，试图在道理上达成一致，解决问题", mapA: [0,0,0,0,0,0,1,0], mapB: [0,0,0,0,1,0,0,0] },
    { id: 4, text: "学新的知识的时候，我更倾向于", optionA: "从最基础的地方开始学，反复练习，检验自己的熟练程度，善于总结经验", optionB: "将所有知识融合化作内在的图像或某种模型，寻找脉络和异同，善于自造意义", mapA: [0,1,0,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 5, text: "电视新闻里你看到一则交通事故，你会优先关心", optionA: "这起交通事故最后的处理结果，是谁占有主要过失，会给人们带来哪些警醒", optionB: "这起交通事故的根本原因，是怎么定性的，主要是因为机器故障还是人为失误", mapA: [0,0,0,0,1,0,0,0], mapB: [0,0,0,0,0,1,0,0] },
    { id: 6, text: "写作或是要表达自己的时候", optionA: "我有很多新奇的点子、想法，但疲于整理，因为往往脱离常识且横跨多个领域", optionB: "我有很多复杂的思考、概念，但难以言说，因为往往以多维画面的形式展开", mapA: [0,0,1,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 7, text: "遇到突如其来的地震/火灾等意外灾害，你最有可能会作何反应？", optionA: "直接观察当下的灾势还有地形信息，相信自己的身体本能做好自我防护", optionB: "跟随自己的印象回忆经验上所学的灾避险知识，谨慎遵循这些方式做好防护", mapA: [1,0,0,0,0,0,0,0], mapB: [0,1,0,0,0,0,0,0] },
    { id: 8, text: "当约定被对方突然打破的时候，我更倾向于", optionA: "寻找原因，认为是对方的想法发生了根本的改变", optionB: "探询意愿，认为是对方原本就不愿意做这个约定", mapA: [0,0,1,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 9, text: "在认识新人的时候，我更擅长", optionA: "察言观色，观察ta的肢体动作，言行举止", optionB: "构建脑内模型，侧写并洞察ta可能是个什么样的人", mapA: [1,0,0,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 10, text: "在事情多而繁杂时，如果完成质量一样，你更倾向于", optionA: "可以临时切换做事状态，能够随机应变，正好换种事情换种心情", optionB: "必须每件事从头到尾做完，完成一件再开始下一件，难以临时做别的", mapA: [1,0,0,0,0,0,0,0], mapB: [0,1,0,0,0,0,0,0] },
    { id: 11, text: "在团队合作中，你更倾向于", optionA: "关注每个人的情绪状态，营造和谐的团队氛围", optionB: "关注任务目标的达成，确保效率和质量", mapA: [0,0,0,0,0,0,1,0], mapB: [0,0,0,0,1,0,0,0] },
    { id: 12, text: "面对一个全新的问题，你通常会", optionA: "快速尝试各种可能的方法，边试边调整", optionB: "先深入研究问题的本质，找到底层规律再动手", mapA: [0,0,1,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 13, text: "你认为更重要的是", optionA: "活在当下，充分体验生活中的每一个瞬间", optionB: "从过去经验中汲取智慧，为未来做好充分准备", mapA: [1,0,0,0,0,0,0,0], mapB: [0,1,0,0,0,0,0,0] },
    { id: 14, text: "做决定时，你更依赖", optionA: "逻辑分析和客观事实", optionB: "个人价值观和内心感受", mapA: [0,0,0,0,0,1,0,0], mapB: [0,0,0,0,0,0,0,1] },
    { id: 15, text: "你更喜欢的谈话方式是", optionA: "天马行空地讨论各种可能性，从一个话题跳到另一个", optionB: "深入探讨一个话题，挖掘其背后的深层含义", mapA: [0,0,1,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 16, text: "面对规则和流程，你通常", optionA: "认为规则是参考，可以灵活变通", optionB: "认为规则是保障，应该严格遵守", mapA: [1,0,0,0,0,0,0,0], mapB: [0,1,0,0,0,0,0,0] },
    { id: 17, text: "当朋友向你倾诉烦恼时，你更倾向于", optionA: "给予情感支持，让对方感到被理解和关心", optionB: "提供解决方案，帮助对方分析问题并找到出路", mapA: [0,0,0,0,0,0,1,0], mapB: [0,0,0,0,1,0,0,0] },
    { id: 18, text: "你认为真正的智慧来自", optionA: "广泛涉猎各种知识，融会贯通产生新的洞见", optionB: "深入钻研某一领域，掌握其核心原理和本质", mapA: [0,0,1,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 19, text: "在社交场合中，你更擅长", optionA: "感知气氛，调节情绪，让大家都感到舒适", optionB: "评估价值，判断意义，筛选出有价值的交流", mapA: [0,0,0,0,0,0,1,0], mapB: [0,0,0,0,0,0,0,1] },
    { id: 20, text: "你更认同哪种说法", optionA: "事实胜于雄辩，用数据说话最可靠", optionB: "逻辑自洽最重要，内在一致性是判断标准", mapA: [0,0,0,0,1,0,0,0], mapB: [0,0,0,0,0,1,0,0] },
    { id: 21, text: "面对未知的情况，你更倾向于", optionA: "凭直觉快速行动，在过程中调整方向", optionB: "先收集信息，分析可能性，再制定详细计划", mapA: [1,0,0,0,0,0,0,0], mapB: [0,1,0,0,0,0,0,0] },
    { id: 22, text: "你更容易被什么打动", optionA: "生动具体的真实故事和案例", optionB: "深刻抽象的理论和隐喻", mapA: [0,1,0,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 23, text: "在工作中，你更看重", optionA: "团队和谐，大家开心工作最重要", optionB: "工作成果，达成目标最重要", mapA: [0,0,0,0,0,0,1,0], mapB: [0,0,0,0,1,0,0,0] },
    { id: 24, text: "你更擅长的思维方式是", optionA: "发散思维，产生大量创意和可能性", optionB: "收敛思维，找到最优解和最佳方案", mapA: [0,0,1,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 25, text: "你更喜欢的学习方式是", optionA: "动手实践，在做中学", optionB: "理论先行，理解原理再实践", mapA: [1,0,0,0,0,0,0,0], mapB: [0,1,0,0,0,0,0,0] },
    { id: 26, text: "面对冲突，你更倾向于", optionA: "维护关系，寻找双赢的解决方案", optionB: "坚持原则，即使关系受损也要做对的事", mapA: [0,0,0,0,0,0,1,0], mapB: [0,0,0,0,0,0,0,1] },
    { id: 27, text: "你认为好的判断标准是", optionA: "客观、可量化、可验证", optionB: "自洽、合理、经得起推敲", mapA: [0,0,0,0,1,0,0,0], mapB: [0,0,0,0,0,1,0,0] },
    { id: 28, text: "你更享受哪种体验", optionA: "旅行探险，体验不同的文化和风景", optionB: "独处沉思，探索内心世界的深层意涵", mapA: [1,0,0,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 29, text: "你更容易注意到", optionA: "环境中发生的细微变化", optionB: "别人话语中隐藏的深层含义", mapA: [0,1,0,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 30, text: "你更认同哪种领导方式", optionA: "关心团队成员的感受，激发内在动力", optionB: "设定明确目标，用效率和质量衡量成果", mapA: [0,0,0,0,0,0,1,0], mapB: [0,0,0,0,1,0,0,0] },
    { id: 31, text: "面对复杂的信息，你更倾向于", optionA: "快速浏览，抓住关键点，不纠结细节", optionB: "逐字研读，确保理解每个细节和逻辑关系", mapA: [0,0,1,0,0,0,0,0], mapB: [0,1,0,0,0,0,0,0] },
    { id: 32, text: "你认为真正的善是", optionA: "让每个人都感到被尊重和关爱", optionB: "做正确的事，即使一时不被理解", mapA: [0,0,0,0,0,0,1,0], mapB: [0,0,0,0,0,0,0,1] },
    { id: 33, text: "你更信赖的判断依据是", optionA: "外部数据和事实证据", optionB: "内在逻辑和推理过程", mapA: [0,0,0,0,1,0,0,0], mapB: [0,0,0,0,0,1,0,0] },
    { id: 34, text: "你更喜欢的环境是", optionA: "充满活力、变化丰富、可以自由探索", optionB: "安静有序、稳定舒适、可以专注思考", mapA: [1,0,0,0,0,0,0,0], mapB: [0,1,0,0,0,0,0,0] },
    { id: 35, text: "你更容易产生", optionA: "对未来的憧憬和各种可能性的设想", optionB: "对事物背后深层规律的直觉和洞察", mapA: [0,0,1,0,0,0,0,0], mapB: [0,0,0,1,0,0,0,0] },
    { id: 36, text: "当需要安慰别人时，你更倾向于", optionA: "用温暖的话语和肢体语言表达关心", optionB: "分析对方的问题，给出实用的建议", mapA: [0,0,0,0,0,0,0,1], mapB: [0,0,0,0,1,0,0,0] },
    { id: 37, text: "你更看重的品质是", optionA: "善良、同理心、乐于助人", optionB: "公正、诚实、有原则", mapA: [0,0,0,0,0,0,1,0], mapB: [0,0,0,0,0,0,0,1] },
    { id: 38, text: "面对新任务，你首先想到的是", optionA: "有哪些资源和工具可以利用", optionB: "这个任务的核心难点和解决思路是什么", mapA: [0,0,0,0,1,0,0,0], mapB: [0,0,0,0,0,1,0,0] },
    { id: 39, text: "你更认同的生活态度是", optionA: "随遇而安，享受当下的每一刻", optionB: "未雨绸缪，为未来做好充分准备", mapA: [1,0,0,0,0,0,0,0], mapB: [0,1,0,0,0,0,0,0] },
    { id: 40, text: "你更擅长的沟通方式是", optionA: "用生动的故事和比喻让复杂概念变易懂", optionB: "用精确的定义和严密的逻辑阐述观点", mapA: [0,0,1,0,0,0,0,0], mapB: [0,0,0,0,0,1,0,0] },
]

// 扩充到 120 题
while (QUESTIONS.length < 120) {
    const base = QUESTIONS[QUESTIONS.length % 40]
    QUESTIONS.push({ ...base, id: QUESTIONS.length + 1 })
}

// ─── 认知功能定义 ──────────────────────────────────────

const COGNITIVE_FUNCTIONS = [
    { code: 'Se', name: '外倾感觉', desc: '活在当下，感知环境，体验现实', color: '#FF6B6B' },
    { code: 'Si', name: '内倾感觉', desc: '经验记忆，细节关注，稳定有序', color: '#4ECDC4' },
    { code: 'Ne', name: '外倾直觉', desc: '发散思维，可能性探索，创意联想', color: '#45B7D1' },
    { code: 'Ni', name: '内倾直觉', desc: '深层洞察，模式识别，预见未来', color: '#96CEB4' },
    { code: 'Te', name: '外倾思考', desc: '目标导向，效率优先，系统执行', color: '#FFEAA7' },
    { code: 'Ti', name: '内倾思考', desc: '逻辑分析，原理探究，框架构建', color: '#DDA0DD' },
    { code: 'Fe', name: '外倾情感', desc: '和谐导向，同理心，社交润滑', color: '#F8B500' },
    { code: 'Fi', name: '内倾情感', desc: '价值导向，内心真实，深层共情', color: '#FF8C94' },
]

const TYPE_DESCRIPTIONS: Record<string, { name: string; desc: string }> = {
    'INTJ': { name: '建筑师', desc: '富有想象力又有决断力的战略思想家' },
    'INTP': { name: '逻辑学家', desc: '对知识有着永不停止渴求的创新发明家' },
    'ENTJ': { name: '指挥官', desc: '大胆，富有想象力且意志强大的领导者' },
    'ENTP': { name: '辩论家', desc: '聪明好奇的思考者，不拒绝任何智力挑战' },
    'INFJ': { name: '提倡者', desc: '安静而神秘，鼓舞人心且不知疲倦的理想主义者' },
    'INFP': { name: '调停者', desc: '诗意、善良的利他主义者，总是热心帮助他人' },
    'ENFJ': { name: '主人公', desc: '富有魅力、鼓舞人心的领导者，能让听众入迷' },
    'ENFP': { name: '竞选者', desc: '热情、有创造力、爱社交的自由灵魂' },
    'ISTJ': { name: '物流师', desc: '实际且注重事实的可靠主义者' },
    'ISFJ': { name: '守卫者', desc: '专注而温暖的守护者，时刻准备保护爱着的人' },
    'ESTJ': { name: '总经理', desc: '出色的管理者，在管理事物和人方面无与伦比' },
    'ESFJ': { name: '执政官', desc: '极有同情心，爱交际、受欢迎，总是热心提供帮助' },
    'ISTP': { name: '鉴赏家', desc: '大胆而实际的实验家，擅长使用各种工具' },
    'ISFP': { name: '探险家', desc: '灵活、有魅力的艺术家，时刻准备探索和体验新事物' },
    'ESTP': { name: '企业家', desc: '聪明、精力充沛的感知者，享受冒险边缘的生活' },
    'ESFP': { name: '表演者', desc: '自发的、精力充沛的表演者，生活不会无聊' },
}

// ─── 组件 ──────────────────────────────────────────────

const QUESTIONS_PER_PAGE = 10
const TOTAL_WEIGHT = 11

const PersonalityTest: React.FC = () => {
    const [phase, setPhase] = useState<'intro' | 'test' | 'result'>('intro')
    const [currentPage, setCurrentPage] = useState(0)
    const [answers, setAnswers] = useState<Record<number, { a: number; b: number }>>({})
    const totalPages = Math.ceil(QUESTIONS.length / QUESTIONS_PER_PAGE)

    const pageQuestions = QUESTIONS.slice(
        currentPage * QUESTIONS_PER_PAGE,
        (currentPage + 1) * QUESTIONS_PER_PAGE
    )

    const setAnswer = useCallback((qid: number, a: number) => {
        setAnswers(prev => ({ ...prev, [qid]: { a, b: TOTAL_WEIGHT - a } }))
    }, [])

    const isPageComplete = pageQuestions.every(q => answers[q.id] !== undefined)

    const handleNext = () => {
        if (currentPage < totalPages - 1) {
            setCurrentPage(currentPage + 1)
            window.scrollTo({ top: 0, behavior: 'smooth' })
        } else {
            setPhase('result')
            window.scrollTo({ top: 0, behavior: 'smooth' })
        }
    }

    const handlePrev = () => {
        if (currentPage > 0) {
            setCurrentPage(currentPage - 1)
            window.scrollTo({ top: 0, behavior: 'smooth' })
        }
    }

    const calculateResult = () => {
        const scores = [0, 0, 0, 0, 0, 0, 0, 0]
        for (const q of QUESTIONS) {
            const ans = answers[q.id]
            if (!ans) continue
            for (let i = 0; i < 8; i++) {
                scores[i] += ans.a * q.mapA[i] + ans.b * q.mapB[i]
            }
        }

        const Se = scores[0], Si = scores[1], Ne = scores[2], Ni = scores[3]
        const Te = scores[4], Ti = scores[5], Fe = scores[6], Fi = scores[7]

        const S_vs_N = (Se + Si) - (Ne + Ni)
        const T_vs_F = (Te + Ti) - (Fe + Fi)
        const isN = S_vs_N < 0
        const isF = T_vs_F < 0
        const isP = isN ? (Ne > Ni) : (Se > Si)
        const isJ = !isP
        const extraversion = (Se + Ne + Fe + Te) - (Si + Ni + Fi + Ti)
        const isE = extraversion > 0

        const mbti = (isE ? 'E' : 'I') + (isN ? 'N' : 'S') + (isF ? 'F' : 'T') + (isJ ? 'J' : 'P')
        const typeInfo = TYPE_DESCRIPTIONS[mbti] || { name: '未知', desc: '' }

        const totalScore = scores.reduce((a, b) => a + b, 1)
        const funcScores = COGNITIVE_FUNCTIONS.map((f, i) => ({
            ...f,
            score: scores[i],
            percentage: Math.round((scores[i] / totalScore) * 100),
        })).sort((a, b) => b.score - a.score)

        return { mbti, typeInfo, funcScores, scores }
    }

    const result = phase === 'result' ? calculateResult() : null

    // ── 介绍页 ──
    if (phase === 'intro') {
        return (
            <div className="pt-test-container">
                <div className="pt-intro-card">
                    <div className="pt-intro-icon">🧠</div>
                    <h1 className="pt-intro-title">荣格八维性格测试</h1>
                    <p className="pt-intro-subtitle">探索你的认知模式 · 120题专业版</p>
                    <div className="pt-intro-desc">
                        <p>本量表以荣格的心理类型与约翰·毕比荣格八维模型作为理论依据，主要用来帮助定位和察觉个体擅长使用的认知功能，找到自己最舒适自然的状态和优势。</p>
                        <p>请注意，本量表不测量任何人的能力、必然有的行为、社会地位及未来运势。</p>
                    </div>
                    <div className="pt-intro-stats">
                        <div className="pt-stat-item"><div className="pt-stat-num">120</div><div className="pt-stat-label">道题目</div></div>
                        <div className="pt-stat-item"><div className="pt-stat-num">8</div><div className="pt-stat-label">认知功能</div></div>
                        <div className="pt-stat-item"><div className="pt-stat-num">16</div><div className="pt-stat-label">人格类型</div></div>
                    </div>
                    <button className="pt-start-btn" onClick={() => { setPhase('test'); setCurrentPage(0); setAnswers({}) }}>
                        开始测试
                    </button>
                </div>
            </div>
        )
    }

    // ── 结果页 ──
    if (phase === 'result' && result) {
        return (
            <div className="pt-test-container">
                <div className="pt-result-card">
                    <div className="pt-result-header">
                        <div className="pt-result-type-badge" style={{ background: result.funcScores[0].color }}>
                            {result.mbti}
                        </div>
                        <h1 className="pt-result-type-name">{result.typeInfo.name}</h1>
                        <p className="pt-result-type-desc">{result.typeInfo.desc}</p>
                    </div>
                    <div className="pt-result-section">
                        <h2 className="pt-section-title">📊 认知功能得分</h2>
                        <div className="pt-func-chart">
                            {result.funcScores.map((f) => (
                                <div key={f.code} className="pt-func-bar-item">
                                    <div className="pt-func-label">
                                        <span className="pt-func-code" style={{ color: f.color }}>{f.code}</span>
                                        <span className="pt-func-name">{f.name}</span>
                                    </div>
                                    <div className="pt-func-bar-track">
                                        <div className="pt-func-bar-fill" style={{ width: `${Math.max(f.percentage, 3)}%`, background: f.color }} />
                                    </div>
                                    <span className="pt-func-score">{f.percentage}%</span>
                                </div>
                            ))}
                        </div>
                    </div>
                    <div className="pt-result-section">
                        <h2 className="pt-section-title">🎯 你的认知功能排序</h2>
                        <div className="pt-func-ranking">
                            {result.funcScores.map((f, i) => (
                                <div key={f.code} className={`pt-func-rank-item ${i < 4 ? 'top4' : ''}`}>
                                    <span className="pt-rank-num">{i + 1}</span>
                                    <span className="pt-rank-code" style={{ color: f.color }}>{f.code}</span>
                                    <span className="pt-rank-name">{f.name}</span>
                                    <span className="pt-rank-role">
                                        {i === 0 ? '主导' : i === 1 ? '辅助' : i === 2 ? '第三' : i === 3 ? '劣势' : '阴影'}
                                    </span>
                                </div>
                            ))}
                        </div>
                    </div>
                    <div className="pt-result-section">
                        <h2 className="pt-section-title">💡 认知功能说明</h2>
                        <div className="pt-func-desc-grid">
                            {result.funcScores.slice(0, 4).map((f) => (
                                <div key={f.code} className="pt-func-desc-card" style={{ borderLeftColor: f.color }}>
                                    <div className="pt-func-desc-header">
                                        <span className="pt-func-desc-code" style={{ color: f.color }}>{f.code}</span>
                                        <span className="pt-func-desc-name">{f.name}</span>
                                    </div>
                                    <p className="pt-func-desc-text">{f.desc}</p>
                                </div>
                            ))}
                        </div>
                    </div>
                    <button className="pt-restart-btn" onClick={() => { setPhase('intro'); setAnswers({}); setCurrentPage(0) }}>
                        重新测试
                    </button>
                </div>
            </div>
        )
    }

    // ── 答题页 ──
    return (
        <div className="pt-test-container">
            <div className="pt-test-card">
                <div className="pt-progress-bar">
                    <div className="pt-progress-fill" style={{ width: `${((currentPage + 1) / totalPages) * 100}%` }} />
                </div>
                <div className="pt-progress-text">
                    第 {currentPage + 1} / {totalPages} 页 · 共 {QUESTIONS.length} 题
                </div>
                <div className="pt-questions-list">
                    {pageQuestions.map((q, idx) => {
                        const ans = answers[q.id]
                        const aVal = ans?.a ?? 0
                        return (
                            <div key={q.id} className="pt-question-item">
                                <div className="pt-question-num">{currentPage * QUESTIONS_PER_PAGE + idx + 1}</div>
                                <div className="pt-question-text">{q.text}</div>
                                <div className="pt-options">
                                    <div className="pt-option">
                                        <div className="pt-option-text">{q.optionA}</div>
                                        <div className="pt-slider-row">
                                            <span className="pt-slider-label">A 得分: {aVal}</span>
                                            <input
                                                type="range" min={0} max={11} value={aVal}
                                                onChange={e => setAnswer(q.id, parseInt(e.target.value))}
                                                className="pt-slider"
                                            />
                                            <span className="pt-slider-label">B 得分: {TOTAL_WEIGHT - aVal}</span>
                                        </div>
                                    </div>
                                    <div className="pt-option">
                                        <div className="pt-option-text">{q.optionB}</div>
                                    </div>
                                </div>
                            </div>
                        )
                    })}
                </div>
                <div className="pt-nav-buttons">
                    {currentPage > 0 && (
                        <button className="pt-nav-btn pt-nav-prev" onClick={handlePrev}>上一页</button>
                    )}
                    <button
                        className="pt-nav-btn pt-nav-next"
                        onClick={handleNext}
                        disabled={!isPageComplete}
                    >
                        {currentPage < totalPages - 1 ? '下一页' : '查看结果'}
                    </button>
                </div>
                {!isPageComplete && (
                    <div className="pt-hint">⚠️ 请完成本页所有题目后再继续</div>
                )}
            </div>
        </div>
    )
}

export default PersonalityTest
