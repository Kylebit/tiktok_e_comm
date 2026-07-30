const SETTLEMENT_WINDOW_DAYS = 366;

// SKU-level aggregates only. No order IDs, customer details, tokens, or raw
// settlement rows are stored in this read-only decision artifact.
const SETTLEMENTS = {
  "0002": {orders:1822, units:2299, customerPaymentMyr:38216.51, actualShippingFeeMyr:-10624.21},
  "0003": {orders:3739, units:3937, customerPaymentMyr:99724.84, actualShippingFeeMyr:-23578.85},
  "0007": {orders:3518, units:5418, customerPaymentMyr:74055.63, actualShippingFeeMyr:-23804.09},
  "0008": {orders:992, units:1276, customerPaymentMyr:19713.95, actualShippingFeeMyr:-5843.35},
  "0009": {orders:384, units:698, customerPaymentMyr:22234.61, actualShippingFeeMyr:-4954.03},
  "0010": {orders:161, units:184, customerPaymentMyr:6109.24, actualShippingFeeMyr:-1256.60},
  "0011": {orders:55, units:65, customerPaymentMyr:2319.08, actualShippingFeeMyr:-485.14},
  "0012": {orders:80, units:89, customerPaymentMyr:3657.02, actualShippingFeeMyr:-663.66},
  "0013": {orders:103, units:114, customerPaymentMyr:5444.34, actualShippingFeeMyr:-1058.00},
  "0014": {orders:348, units:412, customerPaymentMyr:6666.16, actualShippingFeeMyr:-2403.12},
  "0015": {orders:410, units:446, customerPaymentMyr:10559.65, actualShippingFeeMyr:-2064.01},
  "0016": {orders:90, units:97, customerPaymentMyr:2148.37, actualShippingFeeMyr:-607.98},
  "0018": {orders:46, units:48, customerPaymentMyr:803.01, actualShippingFeeMyr:-270.39},
  "0021": {orders:51, units:54, customerPaymentMyr:1183.94, actualShippingFeeMyr:-288.83},
  "0022": {orders:42, units:52, customerPaymentMyr:847.51, actualShippingFeeMyr:-219.22},
  "0118": {orders:24, units:32, customerPaymentMyr:628.89, actualShippingFeeMyr:-134.48},
  "0140": {orders:78, units:86, customerPaymentMyr:1555.24, actualShippingFeeMyr:-481.87},
  "0153": {orders:80, units:85, customerPaymentMyr:1993.63, actualShippingFeeMyr:-553.75},
  "0160": {orders:17, units:23, customerPaymentMyr:617.18, actualShippingFeeMyr:-129.70},
  "0178": {orders:78, units:96, customerPaymentMyr:1900.30, actualShippingFeeMyr:-541.53},
  "0182": {orders:31, units:58, customerPaymentMyr:1526.76, actualShippingFeeMyr:-326.13},
  "0202": {orders:27, units:34, customerPaymentMyr:439.47, actualShippingFeeMyr:-126.91},
  "0203": {orders:14, units:17, customerPaymentMyr:232.57, actualShippingFeeMyr:-79.40},
  "0617": {orders:44, units:57, customerPaymentMyr:1270.34, actualShippingFeeMyr:-317.84}
};

function skuFact(sku, name, stock, available, dimensionsCm, weightG, costCny, recent30, family30 = null) {
  return {
    sku,
    name,
    recent30,
    family30,
    dimensionsCm,
    weightG,
    costCny,
    inventory: {stock, allocated: 0, frozen: 0, inbound: 0, available},
    settlement: {
      days: SETTLEMENT_WINDOW_DAYS,
      orders: SETTLEMENTS[sku]?.orders ?? 0,
      units: SETTLEMENTS[sku]?.units ?? 0,
      customerPaymentMyr: SETTLEMENTS[sku]?.customerPaymentMyr ?? 0,
      actualShippingFeeMyr: SETTLEMENTS[sku]?.actualShippingFeeMyr ?? 0
    }
  };
}

window.SKU_FACTS = [
  skuFact("0002", "龟背竹叶墙贴", 71, 71, [31,3,3], 59, 4.2, 113),
  skuFact("0003", "3片拱门植物墙贴", 636, 636, [29,5,4], 138, 7, 187),
  skuFact("0007", "乡村玫瑰墙贴", 0, 0, [40,4,3], 67, 4, 77),
  skuFact("0008", "绿植藤蔓墙贴", 5, 5, [31,4,4], 80, 5, 79),
  skuFact("0009", "白色木纹墙纸", 190, 190, [45,4,4], 392, 9.5, 38),
  skuFact("0010", "波西米亚曼陀罗桌布 · 规格10", 15, 15, [29,23,3], 206, 15, null, 28),
  skuFact("0011", "波西米亚曼陀罗桌布 · 规格11", 26, 26, [26,21,3], 270, 17, null, 28),
  skuFact("0012", "波西米亚曼陀罗桌布 · 规格12", 13, 13, [28,20,3], 299, 19, null, 28),
  skuFact("0013", "波西米亚曼陀罗桌布 · 规格13", 25, 25, [28,23,4], 383, 27, null, 28),
  skuFact("0014", "拱形假窗绿植墙贴", 106, 106, [26,4,3], 97, 6.5, 1),
  skuFact("0015", "4片冰箱防水垫", 170, 170, [31,4,5], 112, 5, 80),
  skuFact("0016", "2片花卉拱门墙贴", 2, 2, [30,4,4], 147, 9.5, 2),
  skuFact("0018", "3片花瓶干花墙贴", 40, 40, [24,4,4], 100, 5, 2),
  skuFact("0021", "热带绿植盆栽墙贴", 4, 4, [40,3,3], 100, 6.5, 10),
  skuFact("0022", "12片波西米亚花卉墙贴", 0, 0, [40,3,3], 109, 6, 13),
  skuFact("0118", "2片绿植花瓶墙贴", 15, 15, [31,4,4], 154, 7, 2),
  skuFact("0140", "3片盆栽置物架墙贴", 34, 34, [30,4,4], 87, 5, 1),
  skuFact("0153", "2片水彩绿叶墙贴", 40, 40, [32,4,4], 108, 6, 1),
  skuFact("0160", "黑白格纹墙纸", 1, 1, [46,4,4], 246, 4, 2),
  skuFact("0178", "2片热带绿植花瓶墙贴", 46, 46, [32,4,4], 155, 6, 3),
  skuFact("0182", "厨房防油墙贴 · 规格182", 41, 41, [61,4,4], 286, 11, null, 7),
  skuFact("0202", "卡通抽屉垫 · 规格202", 5, 5, [46,6,6], 71, 5, null, 0),
  skuFact("0203", "卡通抽屉垫 · 规格203", 17, 17, [46,6,6], 70, 5, null, 0),
  skuFact("0617", "2支仿真尤加利叶", 13, 13, [22,21,7], 143, 9.5, 1)
];
