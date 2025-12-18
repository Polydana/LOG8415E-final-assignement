import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("results/baseline_sysbench.csv")

# TPS chart
plt.figure()
plt.bar(df['node'], df['tps'])
plt.title('Sysbench TPS - Standalone Baseline')
plt.ylabel('Transactions per second')
plt.savefig("results/baseline_tps.png")

# Avg Latency chart
plt.figure()
plt.bar(df['node'], df['avg_latency_ms'])
plt.title('Average Latency (ms) - Standalone Baseline')
plt.ylabel('ms')
plt.savefig("results/baseline_latency.png")
