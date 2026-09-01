import type { FrameManifest } from "./artifacts";

export function planningPrompt(transcript: string, contract: string): { system: string; user: string } {
  return {
    system: [
      "你是视频学习笔记的证据规划器。只根据字幕规划文章结构与定点截图，不写文章。",
      "必须返回单个严格 JSON 对象，不使用 Markdown 代码块。",
      "时间点必须是数字秒数；不得编造字幕中没有的内容。",
    ].join("\n"),
    user: [
      "请返回：{\"article_outline\":[...],\"frames\":[...]}。",
      "article_outline 必须有 3-8 项，每项包含 section_id、title、start、end、core_claims、learning_goal。",
      "frames 每项包含 section_id、timestamp、purpose、required，通常 6-14 张，最多 24 张。",
      "优先选择实物、步骤、界面、图表、参数、对比和结果；纯口播章节不要硬凑截图。",
      "purpose 必须用中文具体说明画面如何帮助理解对应章节。",
      "\n笔记契约：\n",
      contract,
      "\nSRT 字幕：\n",
      transcript,
    ].join("\n"),
  };
}

export function writingPrompt(
  transcript: string,
  contract: string,
  manifest: FrameManifest,
  vaultFrames: Array<Record<string, unknown>>,
): { system: string; user: string } {
  return {
    system: [
      "你是严谨的中文学习笔记作者。只返回严格 JSON：{\"filename\":\"中文标题 - English Title.md\",\"body\":\"...\"}。",
      "正文完全由字幕支撑，截图只作为对应段落的视觉证据；不确定内容标记“待确认”。",
      "正文以中文为主，禁止顶层 # 标题，必须从 ## 一句话摘要开始。",
    ].join("\n"),
    user: [
      "根据字幕、文章计划和真实截图清单生成可长期复习的完整笔记。",
      "详细内容总结必须逐一使用 article_outline 的 title 作为完全一致的 ### 标题。",
      "在对应论述附近使用清单给出的 obsidian_embed，并在图片前后写中文解释；不得使用未列出的图片。",
      "不要输出 YAML，YAML 将由插件安全保留和更新。不要粘贴完整字幕。",
      "\n笔记契约：\n",
      contract,
      "\n抽帧清单：\n",
      JSON.stringify({ article_outline: manifest.article_outline, frames: vaultFrames }, null, 2),
      "\nSRT 字幕：\n",
      transcript,
    ].join("\n"),
  };
}

export function repairPrompt(
  body: string,
  errors: unknown,
  contract: string,
  manifest: FrameManifest,
  vaultFrames: Array<Record<string, unknown>>,
  filename: string,
): { system: string; user: string } {
  return {
    system: "你只修正现有中文学习笔记的校验问题。返回严格 JSON {\"filename\":\"...\",\"body\":\"...\"}，不得输出 YAML。",
    user: [
      `文件名保持为：${filename}`,
      "只进行一次有针对性的修正，不删除原有有效知识，不重新提取素材。",
      "\n校验错误：\n",
      JSON.stringify(errors, null, 2),
      "\n笔记契约：\n",
      contract,
      "\n允许使用的截图：\n",
      JSON.stringify({ article_outline: manifest.article_outline, frames: vaultFrames }, null, 2),
      "\n当前正文：\n",
      body,
    ].join("\n"),
  };
}
