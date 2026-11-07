#include "..\..\..\System\DCSStrings.h"
#include "..\..\..\System\dcstown_build_left.h"

playside "Empire";

income "gold", 100;
income "stone", 50;
income "life_mana", 50;


/*
buildtree_fighters ""
buildtree_archers  ""
buildtree_mages    ""
buildtree_supports ""
buildtree_special  ""
*/
 
spell "spell_empire_dmg_air";
spell "spell_empire_heal";
spell "spell_empire_buff_dex_mod";
spell "spell_empire_resist_water";
spell "spell_empire_resist_air";
spell "spell_empire_get_rune";
spell "spell_empire_resist_earth";
spell "spell_empire_openfog";
spell "spell_empire_debuff_ini";
spell "spell_empire_summon_livingarmor";
spell "spell_empire_dmg_air_removebuff";
spell "spell_empire_heal_removedebuff";
spell "spell_empire_buff_cover_onblessed";
spell "spell_empire_buff_spd_mod";
spell "spell_empire_resist_fire";
spell "spell_empire_dmg_air_debuff";
spell "spell_empire_resurrect";
spell "spell_empire_buff_agi_mod";
spell "spell_empire_resist_mind";
spell "spell_empire_summon_golem";
spell "spell_empire_heal_agi_mod";
spell "spell_empire_buff_all_mod";
spell "spell_empire_buff_cover";
spell "spell_empire_resist_death";
//allspells;

capital;
icon "Resources\Interface\Icons\towns\icon_empire_town.t";
scene "Resources\Capitals\Empire\capital_empire.scene";
//scene "Resources\Capitals\Empire\capital_empire_without_lod.scene";
char_back_scene "resources\capitals\empire\capital_empire_background_char.scene";
destroy_object "Empire_Capital_Ruined";

camera_track "Camera_01";
//scene_group "";
sound_track "Resources\Music\towns\Empire_Town.ogg";
ambient "$(Sounds)\\imperor_castle_ambient-loop.wav";


leader "empire_leader-warrior";
leader "empire_leader-ranger";
leader "empire_leader-archmage";
leader "empire_leader-thief";

unit "empire_squire";
unit "empire_apprentice";
unit "empire_archer";
unit "empire_acolyte";
unit "empire_titan";

town_nameid "EmpireCapital";

buildtree "fighters"
{
buildtreeid "CapEmpWar";
          building    "empire fighters default";
          {
					level 0;
                    upgrade    "armory";
                    upgrade    "Dungeon";
                    default;

                    camera_track    "Camera_fighters";
                    scene_group    "fighters_default";

                    unit    "empire_squire";

                    gui_id DCSGUIID_ITOWN_BUILDING_0;
	  }
          building    "armory";
          {
					level 1;
                    upgrade    "imperial stable";
                    icon    "Resources\Capitals\Empire\icons\icon_empire_armoury.t";

                    camera_track    "Camera_fighters";
                    scene_group    "armory";


                    unit    "empire_knight";
                    unit_upgrade_from    "empire_squire";

                    nameid    "ArmoryN";
                    descid	"ArmoryD";

                    cost_gold    200;
                    cost_stone   200;

                    gui_id DCSGUIID_ITOWN_BUILDING_1;
	  }
          building    "imperial stable";
          {
					level 2;
                    upgrade    "fountain of life";
                    upgrade    "shrine";
                    icon    "Resources\Capitals\Empire\icons\icon_empire_imperialstable.t";

                    camera_track    "Camera_fighters";
                    scene_group    "imperial_stable";

                    unit    "empire_imperialknight";
                    unit_upgrade_from    "empire_knight";

                    nameid    "ImperialStableN";
                    descid    "ImperialStableD"; 

                    cost_gold    1000;
                    cost_stone    800;

                    gui_id DCSGUIID_ITOWN_BUILDING_2;
	  }
          building    "fountain of life";
          {
					level 3;
                    icon    "Resources\Capitals\Empire\icons\icon_empire_fountainoflife.t";

                    camera_track    "Camera_fighters";
                    scene_group    "fuontain_of_life";

                    unit    "empire_angel";
                    unit_upgrade_from    "empire_imperialknight";

                    nameid    "FountainofLifeN";
                    descid    "FountainofLifeD";

                    cost_gold    2700;
                    cost_stone   1750;

                    gui_id DCSGUIID_ITOWN_BUILDING_3;
         }
          building    "shrine";
         {
					level 3;
                    upgrade    "holy statue";
                    upgrade    "chapel";
                    icon    "Resources\Capitals\Empire\icons\icon_empire_shrine.t";

                    camera_track    "Camera_fighters";
                    scene_group    "shrine";

                    unit    "empire_paladin";
                    unit_upgrade_from    "empire_imperialknight";

                    nameid    "ShrineN";
                    descid    "ShrineD"; 

                    cost_gold    2200;
                    cost_stone   1800;

                    gui_id DCSGUIID_ITOWN_BUILDING_4;
		  }
          building    "holy statue";
          {
					level 4;
                    icon    "Resources\Capitals\Empire\icons\icon_empire_holystatue.t";

                    camera_track    "Camera_fighters";
                    scene_group    "holy_statue";

                    unit    "empire_holyavenger";
                    unit_upgrade_from    "empire_paladin";

                    nameid    "HolyStatueN";
                    descid    "HolyStatueD"; 

                    cost_gold    4400;
                    cost_stone   4400;

                    gui_id DCSGUIID_ITOWN_BUILDING_5;
	      }
          building    "chapel";
          {
					level 4;
                    icon    "Resources\Capitals\Empire\icons\icon_empire_chapel.t";

                    camera_track    "Camera_fighters";
                    scene_group    "chapel";

                    unit    "empire_defenderoffaith";
                    unit_upgrade_from    "empire_paladin";

                    nameid    "ChapelN";
                    descid    "ChapelD"; 

                    cost_gold    4800;
                    cost_stone    4000;

                    gui_id DCSGUIID_ITOWN_BUILDING_6;
	      }
          building    "dungeon";
          {
					level 1;
                    upgrade    "torture chamber";
                    icon    "Resources\Capitals\Empire\icons\icon_empire_dungeon.t";

                    camera_track    "Camera_fighters";
                    scene_group    "dungeon";

                    unit    "empire_witchhunter";
                    unit_upgrade_from    "empire_squire";

                    nameid    "DungeonN";
                    descid    "DungeonD"; 

                    cost_gold    200;
                    cost_stone   300;

                    gui_id DCSGUIID_ITOWN_BUILDING_7;
		  }
          building    "torture chamber";
          {
					level 2;
                    upgrade    "magistrate";
                    icon    "Resources\Capitals\Empire\icons\icon_empire_torturechamber.t";

                    camera_track    "Camera_fighters";
                    scene_group    "torture_chamber";

                    unit    "empire_inquisitor";
                    unit_upgrade_from    "empire_witchhunter";

                    nameid    "TortureChamberN";
                    descid    "TortureChamberD"; 

                    cost_gold    800;
                    cost_stone    1200;

                    gui_id DCSGUIID_ITOWN_BUILDING_8;
		  }
          building    "magistrate";
          {
					level 3;
                    icon    "Resources\Capitals\Empire\icons\icon_empire_magistrate.t";

                    camera_track    "Camera_fighters";
                    scene_group    "magistrate";

                    unit    "empire_grandinquisitor";
                    unit_upgrade_from    "empire_inquisitor";

                    nameid    "MagistrateN";
                    descid    "MagistrateD"; 

                    cost_gold    1800;
                    cost_stone   2500;

                    gui_id DCSGUIID_ITOWN_BUILDING_9;
}
}

buildtree "archers"
{
buildtreeid "CapEmpArc";
          building    "empire archers default";
          {
					level 0;
                    upgrade    "archery range";
                    default;

                    camera_track    "Camera_archers";
                    scene_group    "archers_default";

                    unit    "empire_archer";

                    gui_id DCSGUIID_ITOWN_BUILDING_0;
}
          building    "archery range";
          {
					level 1;
                    upgrade    "imperial guild";
                    icon    "Resources\Capitals\Empire\icons\icon_empire_archeryrange.t";

                    camera_track    "Camera_archers";
                    scene_group    "archery_range";

                    unit    "empire_marksman";
                    unit_upgrade_from    "empire_archer";

                    nameid    "ArcheryRangeN";
                    descid    "ArcheryRangeD";

                    cost_gold    300;
                    cost_stone    200;

                    gui_id DCSGUIID_ITOWN_BUILDING_1;
}
          building    "imperial guild";
          {
					level 2;
                    icon    "Resources\Capitals\Empire\icons\icon_empire_imperialguild.t";

                    camera_track    "Camera_archers";
                    scene_group     "imperial_guild";

                    unit    "empire_imperialassassin";
                    unit_upgrade_from    "empire_marksman";

                    nameid    "ImperialGuildN";
                    descid    "ImperialGuildD"; 

                    cost_gold    1150;
                    cost_stone    750;

                    gui_id DCSGUIID_ITOWN_BUILDING_2;
}
}

buildtree "mages"
{
buildtreeid "CapEmpMag";
          building    "empire mages default";
          {			level 0;
                    upgrade    "library";
                    default;

                    camera_track    "Camera_mages";
                    scene_group    "mage_default";

                    unit    "empire_apprentice";

                    gui_id DCSGUIID_ITOWN_BUILDING_0;
}
          building    "library";
          {			level 1;
                    upgrade    "tower";
                    upgrade    "elemental circle";
                    icon    "Resources\Capitals\Empire\icons\icon_empire_library.t";

                    camera_track    "Camera_mages";
                    scene_group    "library";

                    unit    "empire_mage";
                    unit_upgrade_from    "empire_apprentice";

                    nameid    "LibraryN";
                    descid    "LibraryD";

                    cost_gold    150;
                    cost_stone   250;

                    gui_id DCSGUIID_ITOWN_BUILDING_1;
}
          building    "tower";
          {			level 2;
                    upgrade    "arcane tower";
                    icon    "Resources\Capitals\Empire\icons\icon_empire_tower.t";

                    camera_track    "Camera_mages_02";
                    scene_group     "tower";

                    unit    "empire_wizard";
                    unit_upgrade_from    "empire_mage";

                    nameid    "TowerN";
                    descid    "TowerD";

                    cost_gold    650;
                    cost_stone    950;

                    gui_id DCSGUIID_ITOWN_BUILDING_2;
}

          building    "elemental circle";
          {
					level 2;
                    icon    "Resources\Capitals\Empire\icons\icon_empire_elementalcircle.t";

                    camera_track    "Camera_mages_02";
                    scene_group    "elemental_circle";

                    unit    "empire_elementalist";
                    unit_upgrade_from    "empire_mage";

                    nameid    "ElementalCircleN";
                    descid    "ElementalCircleD";

                    cost_gold    1000;
                    cost_stone    900;

                    gui_id DCSGUIID_ITOWN_BUILDING_3;
}

          building    "arcane tower";
          {
					level 3;			
                    icon    "Resources\Capitals\Empire\icons\icon_empire_arcanetower.t";

                    camera_track    "Camera_mages_02";
                    scene_group    "arcane_tower";

                    unit    "empire_whitewizard";
                    unit_upgrade_from    "empire_wizard";

                    nameid    "ArcaneTowerN";
                    descid    "ArcaneTowerD";

                    cost_gold    1750;
                    cost_stone   2250;

                    gui_id DCSGUIID_ITOWN_BUILDING_4;
}

          building    "ruined temple";
          {
					level 1;
					nolock_tree;
					//default;

                    icon    "Resources\Capitals\Empire\icons\icon_empire_ruinedtemple.t";
					
					//camera_track    "Camera_mages";
                    camera_track    "Camera_ruined_temple";
                    scene_group    "ruined_temple";

                    unit    "empire_titan";
                    //unit_upgrade_from    "";

                    nameid    "RuinedTempleN";
                    descid    "RuinedTempleD"; 

                    cost_gold    2250;
                    cost_stone   900;

                    gui_id DCSGUIID_ITOWN_BUILDING_5;
}
}

buildtree "support"
{
buildtreeid "CapEmpSup";
          building    "empire support default";
          {
					level 0;
                    upgrade    "monastery";
                    upgrade    "church";
                    default;

                    camera_track    "Camera_support";
                    scene_group    "support_default";

                    unit    "empire_acolyte";

                    gui_id DCSGUIID_ITOWN_BUILDING_0;
}
          building    "monastery";
          {
					level 1;
                    upgrade    "sanctuary";
                    icon    "Resources\Capitals\Empire\icons\icon_empire_monastery.t";

                    camera_track    "Camera_support";
                    scene_group    "monastery";

                    unit    "empire_priestess";
                    unit_upgrade_from    "empire_acolyte";

                    nameid    "MonasteryN";
                    descid    "MonasteryD"; 

                    cost_gold    250;
                    cost_stone   150;

                    gui_id DCSGUIID_ITOWN_BUILDING_1;
}
          building    "sanctuary";
          {
					level 2;
                    upgrade    "worship house";
                    icon    "Resources\Capitals\Empire\icons\icon_empire_sanctuary.t";

                    camera_track    "Camera_support";
                    scene_group    "sanctuary";

                    unit    "empire_imperialpriestess";
                    unit_upgrade_from    "empire_priestess";

                    nameid	"SanctuaryN";
                    descid	"SanctuaryD"; 

                    cost_gold    900;
                    cost_stone   700;

                    gui_id DCSGUIID_ITOWN_BUILDING_2;
}
          building    "worship house";
          {
					level 3;
                    icon     "Resources\Capitals\Empire\icons\icon_empire_worshiphause.t";

                    camera_track    "Camera_support";
                    scene_group    "worship";

                    unit    "empire_hierophant";
                    unit_upgrade_from    "empire_imperialpriestess";

                    nameid    "WorshipHouseN";
                    descid    "WorshipHouseD";

                    cost_gold    2300;
                    cost_stone   1700;

                    gui_id DCSGUIID_ITOWN_BUILDING_3;
}
         
          building    "church";
          {
					level 1;
                    upgrade    "basilika";
                    icon    "Resources\Capitals\Empire\icons\icon_empire_church.t";

                    camera_track    "Camera_support";
                    scene_group    "church";

                    unit    "empire_cleric";empire_cleric
                    unit_upgrade_from    "empire_acolyte";

                    nameid    "ChurchN";
                    descid    "ChurchD"; 

                    cost_gold    200;
                    cost_stone   200;

                    gui_id DCSGUIID_ITOWN_BUILDING_4;
}

			building    "basilika";
          {
					level 2;
                    upgrade    "cathedral";
                    icon    "Resources\Capitals\Empire\icons\icon_empire_basilica.t";

                    camera_track    "Camera_support";
                    scene_group    "basilika";

                    unit    "empire_matriarch";
                    unit_upgrade_from    "empire_cleric";

                    nameid    "BasilicaN";
                    descid    "BasilicaD";

                    cost_gold    800;
                    cost_stone   800;

                    gui_id DCSGUIID_ITOWN_BUILDING_5;
}

          building    "cathedral";
          {
					level 3;
                    icon    "Resources\Capitals\Empire\icons\icon_empire_cathedral.t";

                    camera_track    "Camera_support";
                    scene_group    "cathedral";

                    unit    "empire_prophetess";
                    unit_upgrade_from    "empire_matriarch";

                    nameid    "CathedralN";
                    descid    "CathedralD";

                    cost_gold    2000;
                    cost_stone   2000;

                    gui_id DCSGUIID_ITOWN_BUILDING_6;
}
}


buildtree "additional buildings"
{
buildtreeid "CapEmpAdd";
	building "magic tower"
	{
		level 0;
		icon "Resources\Capitals\Empire\icons\icon_empire_magetower.t";
		
		camera_track "Camera_mage_tower";
		scene_group "Mage_Tower";

		nameid "MagicTowerN";
		descid "MagicTowerD";
		icon_big "Resources\Capitals\Empire\icons\icon_empire_magetower_big.t";
		cost_gold 400;
		cost_stone 150;
			

		gui_id DCSGUIID_ITOWN_BUILDING_1;
	}	
	building "temple"
	{
		level 0;
		icon "Resources\Capitals\Empire\icons\icon_empire_temple.t";

		camera_track "Camera_temple";
		scene_group "Temple";

		nameid	"TempleN";
		descid	"TempleD";
		icon_big "Resources\Capitals\Empire\icons\icon_empire_temple_big.t";
		
		cost_gold 250;
		cost_stone 250;

		gui_id DCSGUIID_ITOWN_BUILDING_2;
	}
	building "guild"
	{
		level 0;
		icon "Resources\Capitals\Empire\icons\icon_empire_guild.t";

		camera_track "Camera_guild";
		scene_group "Guild";
		
		unit    "empire_leader-thief";

		nameid "GuildN";
		descid "GuildD";
		icon_big "Resources\Capitals\Empire\icons\icon_empire_guild_big.t";
		
		cost_gold 200;
		cost_stone 350;

		gui_id DCSGUIID_ITOWN_BUILDING_3;
	}

}

shop "Resources\\Profiles\\scripts\\capital_shop.s"