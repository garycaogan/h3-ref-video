# 工作流节点图

`h3-ref2va-turbo.json` 是 RunningHub 上 **user_3ww6n2yr** 发布的公开工作流
[「加速版-MiniMax H3｜多参图像音频极速生视频」](https://www.runninghub.cn/post/2113525277564424193)
（workflowId `2113525277564424193`）的 API 节点图，版权归原作者，**不在本仓库的 MIT 许可范围内**。

附在这里只是为了调用方便：这个工作流用 API 按 id 取节点图会返回 810，提交任务时必须带上整张图。
我们只改了两处：一个没接线的 LoadImage 里残留的作者上传文件名改成了 `None`，示例提示词清空了；
其余和作者发布的一致。作者更新了工作流、或者你想用自己的版本，可以在上面的页面点「下载」拿到新的节点图，
替换这个文件；节点号变了的话 `h3ref/rh.py` 开头的节点表也要跟着改，工具启动时会检查并提示。

模型本身是 MiniMax H3，遵守 [MiniMax H3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)。
