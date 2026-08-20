from __future__ import annotations

import unittest

from project_knowledge.retrieval import KnowledgeAPI


class RetrievalPhase2Tests(unittest.TestCase):
    def test_query_profiles_are_inferred_independently_from_development_intent(self) -> None:
        cases = {
            "严格读取不变量由哪个符号检查？": "invariant",
            "这些组件在哪里注册到 Avatar？": "extension_point",
            "配置默认值在哪里定义？": "configuration",
            "修改登录组件会影响哪些调用方？": "impact",
            "登录入口到账号组件的调用链是什么？": "call_path",
            "Avatar 登录生命周期的基础文件是哪一个？": "call_path",
            "相关测试和核心实现有哪些？": "test_config",
        }

        for task, expected in cases.items():
            with self.subTest(task=task):
                self.assertEqual(KnowledgeAPI._query_profile(task), expected)

    def test_symbol_first_aliases_include_specific_order_methods_and_registry(self) -> None:
        resident = KnowledgeAPI._query_aliases(
            "居民订单缺失时的严格读取不变量由哪个符号检查？"
        )
        customer = KnowledgeAPI._query_aliases("顾客首单已生成状态由哪个符号记录？")
        registry = KnowledgeAPI._query_aliases("Avatar 组件注册表的路径是什么？")
        registration = KnowledgeAPI._query_aliases(
            "garden、resident_order 和 customer_order 组件在哪里注册到 Avatar？"
        )
        login = KnowledgeAPI._query_aliases("玩家登录入口如何处理账号认证？")

        self.assertIn("get_order_strict", resident)
        self.assertIn("set_order", resident)
        self.assertIn("mark_first_order_generated", customer)
        self.assertIn("avatar_def", registry)
        self.assertIn("avatar_def", registration)
        self.assertIn("AccountApi.login", login)
        self.assertIn("AccountComponent.do_login", login)

    def test_query_role_match_is_profile_specific(self) -> None:
        self.assertTrue(
            KnowledgeAPI._query_role_match(
                "invariant",
                "src/app/game/magent/com/resident_order_com.lua",
                "ResidentOrderCom::get_order_strict",
            )
        )
        self.assertTrue(
            KnowledgeAPI._query_role_match(
                "extension_point",
                "src/app/game/magent/avatar/avatar_def.lua",
                "avatar_def",
            )
        )
        self.assertFalse(
            KnowledgeAPI._query_role_match(
                "extension_point",
                "modules/skynet/lualib/socket.lua",
                "read",
            )
        )

    def test_symbol_score_reserves_qualified_boost_for_qualified_queries(self) -> None:
        qualified_score, qualified = KnowledgeAPI._symbol_match_score(
            "GardenCom.start_cultivation 从哪里开始？",
            {
                "id": "src/garden.lua::GardenCom::start_cultivation",
                "qualified_name": "GardenCom::start_cultivation",
                "name": "start_cultivation",
                "kind": "method",
                "path": "src/garden.lua",
                "matched_term": "GardenCom.start_cultivation",
                "recall_channel": "symbol_exact",
            },
        )
        alias_score, alias = KnowledgeAPI._symbol_match_score(
            "garden cultivation",
            {
                "id": "src/dress_up_garden.lua::DressUpGarden",
                "qualified_name": "DressUpGarden",
                "name": "DressUpGarden",
                "kind": "class",
                "path": "src/dress_up_garden.lua",
                "matched_term": "garden",
                "recall_channel": "symbol_alias",
            },
        )

        self.assertEqual(qualified["qualified_name"], 120)
        self.assertEqual(alias["qualified_name"], 0)
        self.assertGreater(qualified_score, alias_score)


if __name__ == "__main__":
    unittest.main()
