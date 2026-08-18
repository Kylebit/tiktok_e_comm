(function (root) {
  const DAY_MS = 24 * 60 * 60 * 1000;

  function parseDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      throw new TypeError("date must be YYYY-MM-DD");
    }
    const timestamp = Date.parse(`${value}T00:00:00Z`);
    if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== value) {
      throw new TypeError("date must be a real calendar date");
    }
    return timestamp;
  }

  function addDays(value, days) {
    if (!Number.isInteger(days) || days < 0) throw new TypeError("days must be a nonnegative integer");
    return new Date(parseDate(value) + days * DAY_MS).toISOString().slice(0, 10);
  }

  function daysBetween(start, end) {
    return Math.max(0, Math.ceil((parseDate(end) - parseDate(start)) / DAY_MS));
  }

  function consume(stock, dailyVelocity, days) {
    if (days <= 0) return stock;
    return Math.max(0, stock - Math.ceil(dailyVelocity * days));
  }

  function consumptionStep({snapshotDate, startDay, endDay, stock, dailyVelocity}) {
    const days = endDay - startDay;
    const demand = Math.ceil(dailyVelocity * days);
    const stockAfter = consume(stock, dailyVelocity, days);
    return {
      kind: "CONSUMPTION",
      fromDate: addDays(snapshotDate, startDay),
      toDate: addDays(snapshotDate, endDay),
      days,
      stockBefore: stock,
      demand,
      stockAfter,
      unmetDemand: Math.max(0, demand - stock)
    };
  }

  function projectSupply({snapshotDate, nextArrivalDate, available, dailyVelocity, inboundEvents}) {
    if (!Number.isInteger(available) || available < 0) throw new TypeError("available must be a nonnegative integer");
    if (typeof dailyVelocity !== "number" || !Number.isFinite(dailyVelocity) || dailyVelocity < 0) {
      throw new TypeError("dailyVelocity must be a nonnegative finite number");
    }
    parseDate(snapshotDate);
    parseDate(nextArrivalDate);
    const horizonDays = daysBetween(snapshotDate, nextArrivalDate);
    const events = (inboundEvents || []).map(event => {
      if (typeof event.batchId !== "string" || !event.batchId.trim()) {
        throw new TypeError("inbound batchId must be a nonempty string");
      }
      if (!Number.isInteger(event.quantity) || event.quantity < 0) {
        throw new TypeError("inbound quantity must be a nonnegative integer");
      }
      parseDate(event.estimatedSellableDate);
      return {...event, day: daysBetween(snapshotDate, event.estimatedSellableDate)};
    }).sort((left, right) => left.day - right.day);

    let stock = available;
    let lastDay = 0;
    let countedInbound = 0;
    let pendingInbound = 0;
    const steps = [];
    events.forEach(event => {
      if (event.day > horizonDays) {
        pendingInbound += event.quantity;
        return;
      }
      if (event.day > lastDay) {
        const step = consumptionStep({
          snapshotDate, startDay: lastDay, endDay: event.day, stock, dailyVelocity
        });
        steps.push(step);
        stock = step.stockAfter;
      }
      const stockBefore = stock;
      stock += event.quantity;
      countedInbound += event.quantity;
      steps.push({
        kind: "INBOUND",
        date: event.estimatedSellableDate,
        batchId: event.batchId,
        quantity: event.quantity,
        stockBefore,
        stockAfter: stock
      });
      lastDay = event.day;
    });
    if (horizonDays > lastDay) {
      const step = consumptionStep({
        snapshotDate, startDay: lastDay, endDay: horizonDays, stock, dailyVelocity
      });
      steps.push(step);
      stock = step.stockAfter;
    }
    return {
      projectedStock: Math.max(0, Math.floor(stock)),
      countedInbound,
      pendingInbound,
      horizonDays,
      events,
      steps,
      projectionMethod: "TIME_PHASED_BATCH_EVENTS_V1"
    };
  }

  root.SUPPLY_CHAIN_TIMELINE = {addDays, daysBetween, projectSupply};
})(typeof window === "undefined" ? globalThis : window);
