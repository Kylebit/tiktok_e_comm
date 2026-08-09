window.SUPPLY_CHAIN_INBOUND_PLAN = {
  capturedAt: "2026-08-09T15:30:00+08:00",
  source: "seaya_oms_inbound_managein_logged_in_readonly",
  regions: {
    MY: {
      batchCount: 1,
      totalUnits: 1000,
      anchorDate: "2026-08-07",
      anchorType: "MARKED_SHIPPED",
      transportDays: 25,
      shelvingDays: 2,
      estimatedSellableDate: "2026-09-03",
      confidence: "ESTIMATED"
    },
    TH: {
      batchCount: 2,
      totalUnits: 3350,
      anchorDate: "2026-08-07",
      anchorType: "LATEST_CREATED_FALLBACK",
      transportDays: 15,
      shelvingDays: 2,
      estimatedSellableDate: "2026-08-24",
      confidence: "CONSERVATIVE_ESTIMATED"
    },
    VN: {
      batchCount: 1,
      totalUnits: 330,
      anchorDate: "2026-08-07",
      anchorType: "CREATED_FALLBACK",
      transportDays: 15,
      shelvingDays: 2,
      estimatedSellableDate: "2026-08-24",
      confidence: "ESTIMATED"
    },
    PH: {
      batchCount: 1,
      totalUnits: 510,
      anchorDate: "2026-08-07",
      anchorType: "CREATED_FALLBACK",
      transportDays: 25,
      shelvingDays: 2,
      estimatedSellableDate: "2026-09-03",
      confidence: "ESTIMATED"
    }
  }
};
