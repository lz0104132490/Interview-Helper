**1. Context / Transcription**

The screenshot shows a LeetCode problem page (Chinese) titled:

> 2141. 同时运行 N 台电脑的最长时间 > (“Maximum Running Time of N Computers”)

Key parts of the statement (summarized from the visible text and standard LeetCode version):

- You have `n` computers and a list `batteries`, where `batteries[i]` is the amount of power (in minutes) that battery `i` can supply. - At any moment you may connect **at most one** battery to a given computer. - Batteries can be swapped between computers **instantly** and can be used on different computers at different times. - You cannot recharge batteries. - You want all `n` computers to run **simultaneously** for the maximum possible number of minutes.

Example 1 shown: `n = 2, batteries = [3, 3, 3]` (actually standard example is `[3,3,3]` or `[3,3,3,3]`; the picture text: `n = 2, batteries = [3,3,3]`). There are diagrams of two computers and three green batteries being plugged / unplugged over time, illustrating that we can schedule batteries so that both computers run together for 4 minutes.

On the right is the C++ code template:

```cpp
class Solution {
 public:
  long long maxRunTime(int n, vector<int>& batteries) {
  }
};
```

**2. Specific question / task**

Implement the function `long long maxRunTime(int n, vector<int>& batteries)` that returns the **maximum number of minutes** all `n` computers can run simultaneously, given that you can swap batteries arbitrarily but cannot recharge them.

---

## 3. Step‑by‑step solution

### 3.1 Observ

