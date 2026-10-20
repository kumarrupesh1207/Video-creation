"""
Project Aurora - Motion Switchboard Routing Validation Suite
Tests semantic keyword routing policies without instantiating heavy ML models.
"""

import logging

from aurora.motion.switchboard import MotionSwitchboard


# Set up clean terminal logging output
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("aurora.test_routing")


def infer_visual_complexity(topic: str) -> str:
    text = (topic or "").strip().lower()
    if any(keyword in text for keyword in ("high action", "combat", "aura")):
        return "high action"
    if any(keyword in text for keyword in ("atmospheric", "subtle", "dreamy")):
        return "atmospheric"
    return "standard"


def run_test_suite():
    # Initialize the lazy-loading switchboard skeleton
    switchboard = MotionSwitchboard({})

    # Define our test vectors mapping stylistic keywords to expected routing targets
    test_cases = [
        {
            "name": "Case 1: High Action Keyword Routing",
            "config": {"motion_mode": "full_i2v_motion", "topic": "An intense anime sword combat scene with high action aura"},
        },
        {
            "name": "Case 2: Atmospheric Keyword Routing",
            "config": {"motion_mode": "full_i2v_motion", "topic": "A slow panning shot of a dreamy, atmospheric landscape"},
        },
        {
            "name": "Case 3: Default I2V Routing Fallback",
            "config": {"motion_mode": "full_i2v_motion", "topic": "A standard character walking down the street"},
        },
        {
            "name": "Case 4: Explicit Direct Mode Routing",
            "config": {"motion_mode": "talking_motion", "topic": "A static podcast interview avatar talking"},
        },
    ]

    logger.info("============ STARTING SWITCHBOARD ROUTING TEST ============")

    for case in test_cases:
        logger.info(f"\n▶ Running {case['name']}...")
        visual_complexity = infer_visual_complexity(case["config"]["topic"])
        resolved_key = switchboard._select_provider_key(
            case["config"]["motion_mode"],
            visual_complexity,
        )
        logger.info(f"  Input Topic: '{case['config']['topic']}'")
        logger.info(f"  Expected Mode: {case['config']['motion_mode']}")
        logger.info(f"  Inferred Visual Complexity: {visual_complexity}")
        logger.info(f"  🎯 Switchboard Resolved Destination: [ {resolved_key} ]")

    logger.info("\n============ ROUTING TEST SUITE COMPLETE ============")


if __name__ == "__main__":
    run_test_suite()
