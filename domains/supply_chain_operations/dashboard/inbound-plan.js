window.SUPPLY_CHAIN_INBOUND_PLAN = {
  capturedAt: "2026-09-02T06:34:25+08:00",
  source: "seaya_oms_inbound_managein_logged_in_readonly",
  regions: {
    MY: {
      totalUnits: 0,
      allocationPolicy: "NO_ACTIVE_BATCH",
      batches: []
    },
    TH: {
      totalUnits: 1270,
      allocationPolicy: "EXACT_BATCH_SKU_REQUIRED",
      batches: [{
        batchId: "THSL4038-60638",
        totalUnits: 1270,
        createdAt: "2026-08-20T10:55:21+08:00",
        anchorDate: "2026-08-22",
        anchorAt: "2026-08-22T16:48:05+08:00",
        estimatedAnchorAt: null,
        anchorType: "REACHED_DOMESTIC_WAREHOUSE",
        inboundStatus: "INBOUND_CONFIRMED",
        transportDays: 15,
        estimatedSellableDate: "2026-09-04",
        estimatedSellableConfirmedAt: "2026-09-01T23:47:53+08:00",
        confidence: "USER_CONFIRMED_BATCH_ETA",
        estimatedSellableSource: "user_confirmed_arrival_and_shelving_date",
        estimatedSellableEvidence: "2026-09-01 explicit confirmation: arrives and shelves in three days",
        anchorEvidence: "seaya_oms_inbound_operation_log_2026-09-02",
        latestMilestoneAt: "2026-08-31",
        latestMilestone: "清关中",
        skuQuantities: {
          "0021": 600,
          "0023": 100,
          "0140": 400,
          "0181": 30,
          "0401": 80,
          "0605": 20,
          "0933": 30,
          "0935": 10
        }
      }]
    },
    VN: {
      totalUnits: 0,
      allocationPolicy: "NO_ACTIVE_BATCH",
      batches: []
    },
    PH: {
      totalUnits: 510,
      allocationPolicy: "EXACT_BATCH_SKU_REQUIRED",
      batches: [{
        batchId: "PHPH4038-59553",
        totalUnits: 510,
        createdAt: "2026-08-07T16:27:07+08:00",
        anchorDate: "2026-08-11",
        anchorAt: "2026-08-11T16:13:16+08:00",
        estimatedAnchorAt: null,
        anchorType: "REACHED_DOMESTIC_WAREHOUSE",
        inboundStatus: "INBOUND_CONFIRMED",
        transportDays: 25,
        estimatedSellableDate: "2026-09-05",
        confidence: "VERIFIED_ANCHOR_ESTIMATED_ETA",
        anchorEvidence: "seaya_oms_inbound_operation_log_2026-09-02",
        latestMilestoneAt: "2026-08-31",
        latestMilestone: "清关中",
        skuQuantities: {
          "0008": 50,
          "0015": 50,
          "0016": 50,
          "0017": 30,
          "0022": 50,
          "0025": 100,
          "0615": 30,
          "0820": 40,
          "0821": 20,
          "0822": 40,
          "0927": 50
        }
      }]
    }
  }
};
