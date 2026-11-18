"""Research paths based on a paper's title and abstract."""

import re


# Direction, subfield, topic, followed by evidence that can also admit a
# supplementary-category paper. Category defaults are for core papers only.
RULES = (
    (("llm", "大语言模型"), ("modeling", "模型与训练"), ("language-models", "语言建模"),
     r"\b(?:large language models?|language model(?:ing|s)?|llms?|foundation language models?)\b"),
    (("llm", "大语言模型"), ("modeling", "模型与训练"), ("reasoning", "推理与对齐"),
     r"\b(?:chain[- ]of[- ]thought|reasoning models?|reasoning traces?|instruction tuning|rlhf)\b"),
    (("llm", "大语言模型"), ("applications", "语言与代码"), ("code-generation", "代码生成"),
     r"\b(?:code generation|program synthesis|code completion|text[- ]to[- ]sql)\b"),
    (("llm", "大语言模型"), ("applications", "语言与代码"), ("nlp", "自然语言处理"),
     r"\b(?:natural language processing|machine translation|text summarization|text classification|named entity recognition)\b"),
    (("llm", "大语言模型"), ("applications", "语言与代码"), ("retrieval", "检索增强与知识"),
     r"\b(?:retrieval[- ]augmented|dense retrieval|neural retrieval|rag pipelines?)\b"),
    (("llm", "大语言模型"), ("evaluation", "通用大模型评测"), ("general", "综合能力"), None),
    (("llm", "大语言模型"), ("evaluation", "通用大模型评测"), ("comparison", "跨模型对比"), None),
    (("vision", "多模态与视觉"), ("representation", "视觉理解"), ("recognition", "识别、检测与分割"),
     r"\b(?:object detection|image classification|image segmentation|visual recognition|computer vision)\b"),
    (("vision", "多模态与视觉"), ("multimodal", "多模态建模"), ("vision-language", "视觉语言模型"),
     r"\b(?:vision[- ]language|visual question answering|multimodal (?:llms?|models?|learning)|image captioning)\b"),
    (("vision", "多模态与视觉"), ("generation", "视觉生成"), ("images-video", "图像与视频生成"),
     r"\b(?:image generation|video generation|text[- ]to[- ]image|text[- ]to[- ]video)\b"),
    (("agents", "智能体与决策"), ("planning-tools", "规划与工具"), ("web-desktop", "网页与桌面操作"),
     r"\b(?:web agents?|browser agents?|gui agents?|web automation|web tasks?|browser navigation|desktop automation|web interaction)\b"),
    (("agents", "智能体与决策"), ("planning-tools", "规划与工具"), ("agents", "智能体规划与工具调用"),
     r"\b(?:ai agents?|llm agents?|tool (?:use|calling)|agentic (?:systems?|workflows?)|language agents?|agent planning)\b"),
    (("agents", "智能体与决策"), ("learning-decisions", "学习与决策"), ("multi-agent", "多智能体协作"),
     r"\b(?:multi[- ]agent|agent coordination)\b"),
    (("agents", "智能体与决策"), ("learning-decisions", "学习与决策"), ("sequential", "序列决策"),
     r"\b(?:reinforcement learning|markov decision processes|policy gradients?)\b"),
    (("robotics", "机器人与具身智能"), ("embodiment", "具身决策"), ("manipulation", "机械臂与操作"),
     r"\b(?:robot(?:ic)? manipulation|robotic grasping|dexterous manipulation|robotic hands?)\b"),
    (("robotics", "机器人与具身智能"), ("embodiment", "具身决策"), ("navigation", "具身导航"),
     r"\b(?:embodied navigation|robot navigation|visual navigation|robot locomotion)\b"),
    (("robotics", "机器人与具身智能"), ("embodiment", "具身决策"), ("robot-learning", "机器人学习"),
     r"\b(?:robot learning|embodied (?:ai|intelligence|agents?)|vision[- ]language[- ]action|robot policies)\b"),
    (("ml", "机器学习基础"), ("algorithms", "学习算法"), ("methods", "模型与学习方法"),
     r"\b(?:machine learning|deep learning|artificial intelligence|neural networks?|predictive models? trained)\b"),
    (("ml", "机器学习基础"), ("algorithms", "学习算法"), ("reinforcement", "强化学习"),
     r"\b(?:reinforcement learning|policy gradients?|q[- ]learning)\b"),
    (("ml", "机器学习基础"), ("theory", "理论与方法"), ("optimization", "优化与泛化"),
     r"\b(?:stochastic gradient descent|generalization bounds?|sample complexity|representation learning)\b"),
    (("systems", "AI 系统与效率"), ("inference", "高效部署"), ("compression", "压缩与低成本推理"),
     r"\b(?:model quantization|llm quantization|model compression|kv[- ]cache|speculative decoding|llm serving)\b"),
    (("systems", "AI 系统与效率"), ("training", "训练系统"), ("distributed", "分布式训练与加速"),
     r"\b(?:distributed training|model parallelism|data parallelism|training throughput|gpu kernels?)\b"),
    (("audio", "语音与音频"), ("speech", "语音建模"), ("recognition", "语音识别与翻译"),
     r"\b(?:speech recognition|speech translation|speech[- ]to[- ]text)\b"),
    (("audio", "语音与音频"), ("speech", "语音建模"), ("synthesis", "语音合成"),
     r"\b(?:speech synthesis|text[- ]to[- ]speech|voice cloning|speech generation)\b"),
    (("audio", "语音与音频"), ("audio-modeling", "音频建模"), ("understanding", "音频理解与生成"),
     r"\b(?:audio (?:language models?|understanding|generation)|music generation|audio classification)\b"),
    (("trust", "可信 AI"), ("safety", "安全与鲁棒性"), ("attacks", "模型安全与防护"),
     r"\b(?:adversarial examples?|prompt injection|jailbreak(?:s|ing)?|model safety|llm safety|ai safety)\b"),
    (("trust", "可信 AI"), ("accountability", "可解释与治理"), ("responsible", "可解释性、公平与隐私"),
     r"\b(?:mechanistic interpretability|model interpretability|algorithmic fairness|fairness in (?:ai|machine learning)|privacy[- ]preserving (?:learning|training)|differentially private (?:learning|training))\b"),
)

TAXONOMY = {}
for *path, _ in RULES:
    level = TAXONOMY
    for identifier, label in path:
        node = level.setdefault(identifier, {"label": label, "children": {}})
        level = node["children"]

PATTERNS = [
    (tuple(identifier for identifier, _ in path), re.compile(pattern, re.IGNORECASE))
    for *path, pattern in RULES if pattern
]
CATEGORY_PATHS = {
    "cs.AI": ("ml", "algorithms", "methods"),
    "cs.LG": ("ml", "algorithms", "methods"),
    "stat.ML": ("ml", "theory", "optimization"),
    "cs.CL": ("llm", "applications", "nlp"),
    "cs.CV": ("vision", "representation", "recognition"),
    "cs.RO": ("robotics", "embodiment", "robot-learning"),
    "cs.MA": ("agents", "learning-decisions", "multi-agent"),
}
LLM_TARGET = r"(?:llms?|(?:large )?language models?|foundation models?)"
EVALUATION_TERM = r"(?:benchmark\w*|evaluat\w*|compar\w*|measur\w*|assess\w*)"
MODEL_EVALUATION = re.compile(
    rf"\b(?:{EVALUATION_TERM} (?:of |for )?(?:general-purpose )?{LLM_TARGET}|"
    rf"{LLM_TARGET} (?:{EVALUATION_TERM}|across))\b", re.IGNORECASE,
)
CROSS_TASK = re.compile(
    r"\b(?:cross[- ]task|multi[- ]?task (?:benchmark|evaluation|accuracy|language understanding)|"
    r"across (?:\w+ ){0,4}(?:tasks|capabilities|domains)|broad (?:capabilities|range of tasks))\b",
    re.IGNORECASE,
)
DOMAIN_TITLE = re.compile(
    r"\b(?:biomedical|clinical|biomolecular|industrial knowledge|linguistic annotation|"
    r"solvers?|federated|mathematical reasoning|financial|legal|embedded software|"
    r"next-step recommendations|multi[- ]agent systems?|workflow orchestration|geographic agents?|"
    r"surgical scene|shopping advice|systematic literature reviews?)\b", re.IGNORECASE,
)
MODEL_COMPARISON = re.compile(r"\b(?:compar\w*|multiple models|model rankings?)\b", re.IGNORECASE)
WEB_AGENT = re.compile(r"\b(?:agents?|large language models?|llms?|machine learning)\b", re.IGNORECASE)


def research_paths(paper, core):
    title, summary = paper["title"], paper["summary"]
    text = f"{title} {summary}"
    generic = ("ml", "algorithms", "methods")
    paths = [
        path for path, pattern in PATTERNS
        if pattern.search(title if not core and path == generic else text)
        and (core or path[-1] != "web-desktop" or WEB_AGENT.search(text))
    ]
    domain = DOMAIN_TITLE.search(title) or any(
        path[0] in {"vision", "robotics", "audio"} or path[-1] in {"web-desktop", "code-generation", "retrieval"}
        for path, pattern in PATTERNS if pattern.search(title)
    )
    if (
        ("llm", "modeling", "language-models") in paths and MODEL_EVALUATION.search(title)
        and CROSS_TASK.search(text) and not domain
    ):
        topic = "comparison" if MODEL_COMPARISON.search(title) else "general"
        paths.append(("llm", "evaluation", topic))
    if not paths and core:
        categories = paper.get("categories", []) + [event["category"] for event in paper["announcements"]]
        paths = sorted({CATEGORY_PATHS[category] for category in categories if category in CATEGORY_PATHS})
        if not paths:
            paths = [CATEGORY_PATHS["cs.AI"]]
    return paths
