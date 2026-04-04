# Minecraft 玩家速度修改方法汇总

本文档整理了从 `MinecraftMotionTools.md` 和 `mineflayer_api.md` 中提取的关于修改和控制玩家速度的各种方法。

---

## 一、Mineflayer API 中的速度控制方法

### 1. 控制状态方法 (Control State)

这是控制机器人移动的主要方法，类似于在 Minecraft 中按下键盘按键。

#### `bot.setControlState(control, state)`

```javascript
bot.setControlState(control, state)
```

**参数：**
- `control` - 控制类型，可选值：`['forward', 'back', 'left', 'right', 'jump', 'sprint', 'sneak']`
- `state` - `true` 或 `false`

**作用：**
- `forward: true` - 向前移动，增加前进方向速度
- `back: true` - 向后移动
- `left: true` / `right: true` - 左右平移
- `jump: true` - 跳跃，给垂直方向一个向上的速度
- `sprint: true` - 疾跑，增加水平移动速度（通常是普通行走的 1.3 倍）
- `sneak: true` - 潜行，降低移动速度并防止跌落

**示例：**
```javascript
// 向前疾跑
bot.setControlState('forward', true);
bot.setControlState('sprint', true);

// 停止移动
bot.setControlState('forward', false);
bot.setControlState('sprint', false);
```

#### `bot.getControlState(control)`

```javascript
const isMoving = bot.getControlState('forward');
```

**作用：** 返回指定控制状态是否激活（true/false）。

#### `bot.clearControlStates()`

```javascript
bot.clearControlStates();
```

**作用：** 关闭所有控制状态，相当于停止所有移动。

#### `bot.controlState`

```javascript
bot.controlState.forward = true;  // 等效于 setControlState('forward', true)
```

**作用：** 直接设置控制状态对象，内部会调用 `bot.setControlState()`。

---

### 2. 物理属性控制

#### `bot.physics`

```javascript
bot.physics.gravity = 0;        // 无重力（飞行模式）
bot.physics.gravity = 0.08;     // 恢复默认重力
```

**作用：** 编辑物理参数来调整重力、跳跃速度、终端速度等。

**警告：** 修改这些参数有一定风险，需谨慎使用。

#### `bot.physicsEnabled`

```javascript
bot.physicsEnabled = false;  // 禁用物理
bot.physicsEnabled = true;   // 启用物理（默认）
```

**作用：** 控制机器人是否受物理影响。禁用后机器人将不受重力、速度等物理规则约束。

---

### 3. 实体速度属性

#### `bot.entity.velocity`

```javascript
// 获取当前速度
const velocity = bot.entity.velocity;
console.log(velocity.x, velocity.y, velocity.z);

// 注意：在创造模式飞行时，此值可能不准确
```

**作用：** 
- 读取机器人当前的速度向量（Vec3）
- 包含 x（南北）、y（垂直）、z（东西）三个方向的速度分量
- 当机器人移动时触发 `"move"` 事件，可以通过 `bot.entity.position.minus(bot.entity.velocity)` 获取上一帧位置

**相关事件：**
```javascript
bot.on('move', () => {
  const currentPos = bot.entity.position;
  const prevPos = currentPos.minus(bot.entity.velocity);
  console.log('移动速度:', bot.entity.velocity);
});

bot.on('forcedMove', () => {
  // 被服务器强制移动（传送、重生等）
  console.log('新位置:', bot.entity.position);
});
```

---

### 4. 创造模式飞行控制

#### `bot.creative.startFlying()`

```javascript
await bot.creative.startFlying();
```

**作用：**
- 将 `bot.physics.gravity` 设置为 `0`
- 启用飞行模式，玩家可以在空中自由移动
- 适用于创造模式

**注意：** 飞行时 `bot.entity.velocity` 不会准确更新。

#### `bot.creative.stopFlying()`

```javascript
await bot.creative.stopFlying();
```

**作用：**
- 恢复 `bot.physics.gravity` 到原始值
- 退出飞行模式，恢复正常物理

#### `bot.creative.flyTo(destination)`

```javascript
const destination = new Vec3(x, y, z);
await bot.creative.flyTo(destination);
```

**作用：**
- 自动调用 `startFlying()`
- 以恒定速度沿直线飞往目标点
- 如果途中有障碍物会失败
- 建议每次飞行距离 `< 2` 个方块

**注意：** 此方法不进行路径寻找，仅直线飞行。

---

### 5. 鞘翅飞行控制

#### `bot.elytraFly()`

```javascript
await bot.elytraFly();
```

**作用：**
- 激活鞘翅飞行
- 返回 Promise，激活完成后解决
- 如果失败会抛出错误

**相关属性：**

#### `bot.fireworkRocketDuration`

```javascript
console.log(bot.fireworkRocketDuration);  // 剩余的烟花加速刻数
```

**作用：**
- 表示烟花火箭提供的加速剩余时间（以物理刻为单位）
- 使用烟花可以临时增加飞行速度

**相关事件：**
```javascript
bot.on('entityElytraFlew', (entity) => {
  console.log(entity.username + ' 开始鞘翅飞行');
});

bot.on('usedFirework', (fireworkEntityId) => {
  console.log('使用了烟花火箭加速');
});
```

---

### 6. 载具移动控制

#### `bot.moveVehicle(left, forward)`

```javascript
bot.moveVehicle(1, 1);   // 向左前方移动
bot.moveVehicle(-1, 1);  // 向右前方移动
bot.moveVehicle(0, -1);  // 向后移动
```

**参数：**
- `left` - `-1`（右）或 `1`（左）
- `forward` - `-1`（后）或 `1`（前）

**作用：**
- 控制载具（船、矿车等）的移动
- 方向相对于玩家的朝向

**相关方法：**
```javascript
await bot.mount(entity);    // 骑上载具
bot.dismount();             // 下载具
```

---

### 7. 朝向控制（影响移动方向）

#### `bot.look(yaw, pitch, force)`

```javascript
await bot.look(yaw, pitch, true);
```

**参数：**
- `yaw` - 水平旋转角度（弧度），从正东开始，逆时针增加
- `pitch` - 俯仰角度（弧度），0为水平，π/2为向上，-π/2为向下
- `force` - 如果为 true，跳过平滑过渡，立即设置朝向

**作用：**
- 设置头部朝向，影响 `forward` 等控制状态的前进方向

#### `bot.lookAt(point, force)`

```javascript
await bot.lookAt(new Vec3(x, y, z), true);
```

**作用：**
- 使头部面向指定点
- 常与 `setControlState` 配合使用来控制移动方向

---

## 二、MinecraftMotionTools 速度计算方法

MinecraftMotionTools 是一个用于预测 Minecraft 中实体速度和位置的 Python 库。

### 安装

```bash
pip install MinecraftMotionTools
```

### 核心概念

Minecraft 的速度更新公式：

```
position_new = position_old + velocity - k * acceleration
```

其中 `k` 是加速度阻力系数，用于处理不同的更新顺序（P-A-D 或 A-P-D）。

### 实体参数表

| 类型 | 加速度 | 垂直阻力 | 水平阻力 | 阻力应用时机 | k 系数 |
|------|--------|----------|----------|--------------|--------|
| 玩家、生物、盔甲架 | 0.08 | 0.02 | 0.09 | 之后 | 0 |
| 缓降状态 | 0.01 | 0.02 | 0.09 | 之后 | 0 |
| 物品、掉落方块、TNT | 0.04 | 0.02 | 0.02 | 之后 | 1 |
| 矿车 | 0.04 | 0.05 | 0.05 | 之后 | 1 |
| 船 | 0.04 | 0 | 0.10 | - | - |
| 投掷物（鸡蛋、雪球、末影珍珠） | 0.03 | 0.01 | 0.01 | 之前 | 0 |
| 药水 | 0.05 | 0.01 | 0.01 | 之前 | 0 |
| 经验瓶 | 0.07 | 0.01 | 0.01 | 之前 | 0 |
| 经验球 | 0.03 | 0.02 | 0.02 | 之后 | 1 |
| 箭、三叉戟 | 0.05 | 0.01 | 0.01 | 之前 | 0 |
| 火球 | 0.10 | 0.05 | 0.05 | 之后 | 0 |
| 风弹 | 0.10 | 0 | 0 | - | - |

**重要限制：**
- 最大速度限制为 10，超过会被重置为 0
- 矿车最大水平速度为 0.4
- 阻力为 0 的实体可以无限加速（如风弹）

### 常用函数

#### 1. 从初始速度和时间计算当前速度

```python
from MinecraftMotionTools import v_from_t

# 默认参数为掉落方块
v = v_from_t(v0=1.0, t=20)  # v0: 初始速度, t: 刻数

# 自定义参数
v = v_from_t(v0=1.0, t=20, a=0.08, d=0.02, after=True)
```

#### 2. 计算相对位置

```python
from MinecraftMotionTools import p_from_t

p = p_from_t(v0=1.0, t=20, a=0.08, d=0.02, after=True, k=0)
```

#### 3. 计算达到最大高度的时间和高度

```python
from MinecraftMotionTools import max_height_tick_from_v0, max_height_from_v0

# 达到最高点所需刻数
tick = max_height_tick_from_v0(v0=1.0, a=0.08, d=0.02, after=True, k=0)

# 最大高度
height = max_height_from_v0(v0=1.0, a=0.08, d=0.02, after=True, k=0)
```

#### 4. 从最大高度反推初始速度

```python
from MinecraftMotionTools import v0_from_max_height

solutions = v0_from_max_height(h=5.0, a=0.08, d=0.02, after=True, k=0)
# 返回最多 2 个解，对应不同的抛物线轨迹
```

#### 5. 从当前状态反推初始速度

```python
from MinecraftMotionTools import v0_from_v_t, v0_from_p_t

# 从当前速度和时间
v0 = v0_from_v_t(v=0.5, t=20, a=0.08, d=0.02, after=True)

# 从当前位置和时间
v0 = v0_from_p_t(p=10.0, t=20, a=0.08, d=0.02, after=True, k=0)
```

#### 6. 计算到达特定状态的时间

```python
from MinecraftMotionTools import t_from_v0_v, t_from_v0_p

# 从速度变化计算时间
t = t_from_v0_v(v0=1.0, v=0.5, a=0.08, d=0.02, after=True)

# 从位置变化计算时间
t = t_from_v0_p(v0=1.0, p=10.0, a=0.08, d=0.02, after=True, k=0)
```

#### 7. 综合求解

```python
from MinecraftMotionTools import v0_t_from_v_p

# 同时求解初始速度和时间
v0, t = v0_t_from_v_p(v=0.5, p=10.0, a=0.08, d=0.02, after=True, k=0)
```

### 加速度相关函数

用于从两个状态反推加速度：

```python
from MinecraftMotionTools import (
    a_from_double_v_t,    # 从两个速度/时间状态
    a_from_double_p_t,    # 从两个位置/时间状态
    a_from_double_v_p,    # 从两个速度/位置状态
    a_from_v_t_p_t,       # 从速度/时间和位置/时间状态
    a_from_v_t_v_p,       # 从速度/时间和速度/位置状态
    a_from_p_t_v_p        # 从位置/时间和速度/位置状态
)

# 示例：从两个速度/时间状态计算加速度
a = a_from_double_v_t(
    (v1=1.0, t1=0), 
    (v2=0.5, t2=20), 
    d=0.02, 
    after=True
)
```

**注意：** 当阻力 `d=0` 时，函数会使用安全算法（非迭代近似）。

---

## 三、速度控制策略总结

### 行走/奔跑速度控制

```javascript
// 普通行走
bot.setControlState('forward', true);

// 疾跑（更快）
bot.setControlState('forward', true);
bot.setControlState('sprint', true);

// 潜行（更慢，防跌落）
bot.setControlState('sneak', true);
bot.setControlState('forward', true);
```

### 垂直速度控制

```javascript
// 跳跃（瞬时向上速度）
bot.setControlState('jump', true);
setTimeout(() => bot.setControlState('jump', false), 100);

// 创造模式飞行（无重力）
await bot.creative.startFlying();
bot.setControlState('jump', true);  // 向上飞
bot.setControlState('sneak', true); // 向下飞

// 鞘翅飞行
await bot.elytraFly();
// 使用烟花加速
bot.fireworkRocketDuration;  // 查看剩余加速时间
```

### 载具速度控制

```javascript
// 船/矿车
await bot.mount(vehicle);
bot.moveVehicle(1, 1);   // 左前方
bot.moveVehicle(0, 1);   // 正前方
bot.dismount();
```

### 物理参数调整（高级）

```javascript
// 调整重力（影响下落速度）
bot.physics.gravity = 0.04;  // 一半重力

// 禁用物理（完全控制）
bot.physicsEnabled = false;
```

---

## 四、注意事项

1. **速度限制：** Minecraft 中最大速度为 10，超过会被重置为 0
2. **矿车速度：** 最大水平速度限制为 0.4
3. **飞行时速度：** 创造模式飞行时 `bot.entity.velocity` 不准确
4. **阻力为 0：** 风弹等实体可以无限加速
5. **OnGround 状态：** 玩家在地面时的水平阻力取决于站立方块的滑度，默认为 0.454
6. **physicsTick：** 每刻触发一次，可用于精确控制速度相关的逻辑

```javascript
bot.on('physicsTick', () => {
  // 每刻执行的速度相关逻辑
  console.log('速度:', bot.entity.velocity);
});
```
