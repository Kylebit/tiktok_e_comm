window.SUPPLY_CHAIN_INBOUND_PLAN = {
  capturedAt: "2026-08-09T15:30:00+08:00",
  source: "seaya_oms_inbound_managein_logged_in_readonly",
  regions: {
    MY: {
      totalUnits: 1000,
      allocationPolicy: "SINGLE_ACTIVE_BATCH",
      batches: [{
        batchId: "MYSH4038-59560",
        totalUnits: 1000,
        createdAt: "2026-08-07T16:54:05+08:00",
        anchorDate: null,
        anchorAt: null,
        estimatedAnchorAt: "2026-08-11T16:54:05+08:00",
        anchorType: "CREATED_PLUS_4_DAYS_ESTIMATE",
        inboundStatus: "NOT_YET_INBOUND",
        transportDays: 25,
        estimatedSellableDate: "2026-09-05",
        confidence: "USER_APPROVED_ESTIMATED_ANCHOR"
      }]
    },
    TH: {
      totalUnits: 3350,
      allocationPolicy: "EXACT_BATCH_SKU_REQUIRED",
      batches: [{
        batchId: "THML4038-58701",
        totalUnits: 2100,
        createdAt: "2026-07-29T09:20:43+08:00",
        anchorDate: "2026-08-04",
        anchorAt: "2026-08-04T15:39:15+08:00",
        estimatedAnchorAt: null,
        anchorType: "REACHED_DOMESTIC_WAREHOUSE",
        inboundStatus: "INBOUND_CONFIRMED",
        transportDays: 15,
        estimatedSellableDate: "2026-08-19",
        confidence: "VERIFIED_ANCHOR_ESTIMATED_ETA",
        anchorEvidence: "user_supplied_seaya_inbound_log_screenshot_2026-08-09",
        skuQuantities: {
          "0003": 100,
          "0007": 100,
          "0008": 100,
          "0015": 400,
          "0016": 50,
          "0018": 50,
          "0021": 200,
          "0023": 50,
          "0026": 600,
          "0140": 200,
          "0178": 100,
          "0198": 100,
          "0223": 50
        }
      }, {
        batchId: "THSL4038-59557",
        totalUnits: 1250,
        createdAt: "2026-08-07T16:41:32+08:00",
        anchorDate: null,
        anchorAt: null,
        estimatedAnchorAt: "2026-08-11T16:41:32+08:00",
        anchorType: "CREATED_PLUS_4_DAYS_ESTIMATE",
        inboundStatus: "NOT_YET_INBOUND",
        transportDays: 15,
        estimatedSellableDate: "2026-08-26",
        confidence: "USER_APPROVED_ESTIMATED_ANCHOR",
        skuQuantities: {
          "0017": 200,
          "0021": 600,
          "0023": 100,
          "0026": 200,
          "0160": 30,
          "0401": 60,
          "0927": 50,
          "0935": 10
        }
      }]
    },
    VN: {
      totalUnits: 330,
      allocationPolicy: "SINGLE_ACTIVE_BATCH",
      batches: [{
        batchId: "VNML4038-59508",
        totalUnits: 330,
        createdAt: "2026-08-07T13:30:11+08:00",
        anchorDate: null,
        anchorAt: null,
        estimatedAnchorAt: "2026-08-11T13:30:11+08:00",
        anchorType: "CREATED_PLUS_4_DAYS_ESTIMATE",
        inboundStatus: "NOT_YET_INBOUND",
        transportDays: 15,
        estimatedSellableDate: "2026-08-26",
        confidence: "USER_APPROVED_ESTIMATED_ANCHOR"
      }]
    },
    PH: {
      totalUnits: 510,
      allocationPolicy: "SINGLE_ACTIVE_BATCH",
      batches: [{
        batchId: "PHPH4038-59553",
        totalUnits: 510,
        createdAt: "2026-08-07T16:27:07+08:00",
        anchorDate: null,
        anchorAt: null,
        estimatedAnchorAt: "2026-08-11T16:27:07+08:00",
        anchorType: "CREATED_PLUS_4_DAYS_ESTIMATE",
        inboundStatus: "NOT_YET_INBOUND",
        transportDays: 25,
        estimatedSellableDate: "2026-09-05",
        confidence: "USER_APPROVED_ESTIMATED_ANCHOR"
      }]
    }
  }
};
