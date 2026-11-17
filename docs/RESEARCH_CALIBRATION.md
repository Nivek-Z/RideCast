# 本轮校准与空间建模文献笔记

检索日期：2026-11-17。使用原始论文、作者预印本、会议论文页及官方文档。Roberts论文打开全文；其余论文主要核对摘要和方法说明，未声称完整复现实验。没有据相似主题推断老师文件的年份、来源或采样口径。

## 空间验证与适用范围

Roberts等（2017），[Cross-validation strategies for data with temporal, spatial, hierarchical, or phylogenetic structure](https://doi.org/10.1111/ecog.02881)，Ecography。论文讨论相关样本混入训练/验证可能产生乐观估计。本轮采用整块留一空间交叉验证；相邻块依然相关、一个块仅2区，因此仍不是充分独立的泛化检验。另有[作者所在大学的全文](https://www.biom.uni-freiburg.de/mitarbeiter/dormann/roberts-et-al-2017-ecography.pdf/at_download/file)。

Meyer、Pebesma（2021），[Predicting into unknown space? Estimating the area of applicability of spatial prediction models](https://doi.org/10.1111/2041-210X.13650)，Methods in Ecology and Evolution；[作者预印本](https://arxiv.org/abs/2005.07939)。其适用域方法强调训练数据在预测因子空间中的支持。本轮借鉴此思想，加入按训练空间折确定阈值的近邻距离诊断。实现使用无权标准化距离，**不是完整的变量重要性加权AOA复现**，不将域内预测解释为有保证。

## 出行需求的非线性和空间差异

Zhang等（2023），[Examining spatial heterogeneity of ridesourcing demand determinants with explainable machine learning](https://doi.org/10.1016/j.jtrangeo.2023.103782)，Journal of Transport Geography；[2022年预印本](https://arxiv.org/abs/2209.07980)。Chicago研究指出不同空间环境下，建成环境与出行量关系可能不同且非线性。本轮因此比较平滑非线性与受约束提升树，并将学到的关系称为条件关联。没有复现其区域分型、解释算法或论文成绩，也没有当前文件足够的机场/商业区用途标签。

Grinsztajn、Oyallon、Varoquaux（2022），[Why do tree-based models still outperform deep learning on typical tabular data?](https://proceedings.nips.cc/paper_files/paper/2022/hash/0378c7692da36807bdec87ab043cdadc-Abstract-Datasets_and_Benchmarks.html)，NeurIPS Datasets and Benchmarks。该基准为将树模型作为表格任务强对照提供依据；它不保证树在本文件上最优。本轮同时比较线性计数、平滑加性和树模型，开发区实际选中平滑模型。

## 合成数据效用不能由样本数量代替

Manousakas、Aydöre（2023），[On the Usefulness of Synthetic Tabular Data Generation](https://arxiv.org/abs/2306.15636)，ICML DMLR Workshop预印本。其多用例比较未支持对表格合成训练收益作普遍保证。本轮保留真实训练、生成训练和混合训练对照；生成路线接近教师，只能说明部分保真，不能据略低的单次测试TV断言额外收益。这篇工作也不能证明所有生成方法无用。

Qian、Davis、van der Schaar（2023），[Synthcity: a benchmark framework for diverse use cases of tabular synthetic data](https://proceedings.neurips.cc/paper_files/paper/2023/hash/09723c9f291f6056fd1885081859c186-Abstract.html)，NeurIPS Datasets and Benchmarks。其用例化评价思路支持区分生成分布的保真度和下游预测效用。本轮分别报告学生对教师的空间分布差异和真实对照区指标，未安装Synthcity或复现其生成器基准。

## 实现依据

固定使用scikit-learn1.7.2的[PoissonRegressor](https://scikit-learn.org/1.7/modules/generated/sklearn.linear_model.PoissonRegressor.html)、[SplineTransformer](https://scikit-learn.org/1.7/modules/generated/sklearn.preprocessing.SplineTransformer.html)和[HistGradientBoostingRegressor](https://scikit-learn.org/1.7/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html)。计数均值用对数链接及L2正则，样条固定4结点和三次基函数；提升树仅对人口与就业密度施加正向单调约束。这些算法约束不等于现实因果规律，文献也不能替代本项目对新数据的验证。
