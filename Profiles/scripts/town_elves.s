#include "..\..\..\System\DCSStrings.h"
#include "..\..\..\System\dcstown_build_left.h"

playside "Elves";

income "gold", 100;
income "stone", 50;
income "grove_mana", 50;

spell "spell_elves_buff_hp";
spell "spell_elves_buff_movwm";
spell "spell_elves_get_potion";
spell "spell_elves_debuff_cover";
spell "spell_elves_debuff_ini";
spell "spell_elves_dmg_earth";
spell "spell_elves_buff_flee_removedebuff";
spell "spell_elves_buff_crit";
spell "spell_elves_debuff_movwm";
spell "spell_elves_summon_earthelemental";
spell "spell_elves_heal";
spell "spell_elves_get_skill_earth";
spell "spell_elves_ignore_vulnerability";
spell "spell_elves_openfog";
spell "spell_elves_summon_ent";
spell "spell_elves_dmg_air";
spell "spell_elves_buff_regen";
spell "spell_elves_buff_dmg";
spell "spell_elves_debuff_range";
spell "spell_elves_summon_unicorn";
spell "spell_elves_dmg_water";
spell "spell_elves_resurrect";
spell "spell_elves_debuff_dmg";
spell "spell_elves_summon_verdant";
//allspells;

capital;
icon "Resources\Interface\Icons\towns\icon_elves_town.t";
scene "Resources\Capitals\Alliance\capital_alliance.scene";
char_back_scene "Resources\Capitals\Alliance\capital_alliance_background_char.scene";
destroy_object "Elves_capital_ruined";

camera_track "Camera_01";
//scene_group "";
sound_track "Resources\Music\towns\Elves_town.ogg";
ambient "$(Sounds)\\elves_castle_ambient-loop.wav";

leader "elves_leader-guardian";
leader "elves_leader-forestliege";
leader "elves_leader-dryad";
leader "elves_leader-thief";

unit "elves_centaurlancer";
unit "elves_adept";
unit "elves_scout";
unit "elves_spiritess";
unit "elves_griffin";

town_nameid "ElvenCapital";

buildtree "fighters"
{
buildtreeid "CapElvWar";
	building "elves fighters default"
	{level 0;
		upgrade "centaur hollow";
		upgrade "centaur stables";
		default;
		
		camera_track "Camera_fighters";
		scene_group "Fighters_default";
		
		unit "elves_centaurlancer";
		gui_id DCSGUIID_ITOWN_BUILDING_0;
	}
	building "centaur hollow"
	{level 1;
		upgrade "tribal ground";
		icon "Resources\Capitals\Alliance\icons\icon_elves_centaurhollow.t";		

		camera_track "Camera_Unicorn_Hollow";
		scene_group "Unicorn_Hollow";
		
		unit "elves_centaurstrider";
		unit_upgrade_from "elves_centaurlancer";
		
		nameid "CentaurHollowN";
		descid "CentaurHollowD"; 
		
		cost_gold  300;
		cost_stone 200;
		
		gui_id DCSGUIID_ITOWN_BUILDING_1;
	}
	building "tribal ground"
	{
		level 2;		
		icon "Resources\Capitals\Alliance\icons\icon_elves_tribalground.t";	

		camera_track "Camera_Tribal_Ground";
		scene_group "Tribal_Ground";
		
		unit "elves_centaursavage";
		unit_upgrade_from "elves_centaurstrider";
		
		nameid "TribalGroundN";
		descid "TribalGroundD"; 
		
		cost_gold 1050;
		cost_stone 750;
		
		gui_id DCSGUIID_ITOWN_BUILDING_2;
	}
	building "centaur stables"
	{
		level 1;
		icon "Resources\Capitals\Alliance\icons\icon_elves_centaurstables.t";	

		camera_track "Camera_Unicorn_Stables";
		scene_group "Unicorn_Stables";
		
		unit "elves_centaurcharger";
		unit_upgrade_from "elves_centaurlancer";
		
		nameid "CentaurStablesN";
		descid "CentaurStablesD"; 
		
		cost_gold 250;
		cost_stone 400;
		
		gui_id DCSGUIID_ITOWN_BUILDING_3;
	}
}


buildtree "archers"
{
buildtreeid "CapElvArc";
	building "elves archers default"
	{
		level 0;
		upgrade "skinner's hutch";
		upgrade "lookout";
		default;

		camera_track "Camera_archers";
		scene_group "Archers_(default)";

		unit "elves_scout";
		gui_id DCSGUIID_ITOWN_BUILDING_0;
	}
	building "skinner's hutch"
	{
		level 1;
		upgrade "hovel";
		upgrade "lodge";
		icon "Resources\Capitals\Alliance\icons\icon_elves_skinnershutch.t";
		
		camera_track "Camera_Skinner_Hutch";
		scene_group "Skinner_Hutch";

		unit "elves_hunter";
		unit_upgrade_from "elves_scout";
		
		nameid "SkinnerHutchN";
		descid "SkinnerHutchD";
		
		cost_gold 200;
		cost_stone 200;
		
		gui_id DCSGUIID_ITOWN_BUILDING_1;		
	}

	building "hovel"
	{
		level 2;
		upgrade "hideout";
		icon "Resources\Capitals\Alliance\icons\icon_elves_hovel.t";
		
		camera_track "Camera_Hovel";
		scene_group "Hovel";

		unit "elves_bandit";
		unit_upgrade_from "elves_hunter";
		
		nameid "HovelN";
		descid "HovelD"; 
		
		cost_gold 800;
		cost_stone 900;
		
		gui_id DCSGUIID_ITOWN_BUILDING_2;		
	}

	building "lodge"
	{
		level 2;
		icon "Resources\Capitals\Alliance\icons\icon_elves_lodge.t";
		
		camera_track "Camera_Lodge";
		scene_group "Lodge";

		unit "elves_stinger";
		unit_upgrade_from "elves_hunter";
		
		nameid "LodgeN";
		descid "LodgeD";
		
		cost_gold 700;
		cost_stone 1200;
		
		gui_id DCSGUIID_ITOWN_BUILDING_3;
	}
	building "hideout"
	{
		level 3;
		upgrade "harborage";
		icon "Resources\Capitals\Alliance\icons\icon_elves_hideout.t";
		
		camera_track "Camera_Hideout";
		scene_group "Hideout";

		unit "elves_brigand";
		unit_upgrade_from "elves_bandit";
		
		nameid "HideoutN";
		descid "HideoutD";
		
		cost_gold 2000;
		cost_stone 1850;
		
		gui_id DCSGUIID_ITOWN_BUILDING_4;
		
	}
	building "harborage"
	{
		level 4;
		icon "Resources\Capitals\Alliance\icons\icon_elves_harborage.t";
		
		camera_track "Camera_Harborage";
		scene_group "Harborage";

		unit "elves_marauder";
		unit_upgrade_from "elves_brigand";
		
		nameid "HarborageN";
		descid "HarborageD";
		
		cost_gold 4600;
		cost_stone 4200;
		
		gui_id DCSGUIID_ITOWN_BUILDING_5;
	}
	building "lookout"
	{
		level 1;
		upgrade "pyromantium";
		upgrade "hydromantium";
		icon "Resources\Capitals\Alliance\icons\icon_elves_lookout.t";
		
		camera_track "Camera_Lookout";
		scene_group "Lookout";

		unit "elves_watchman";
		unit_upgrade_from "elves_scout";
		
		nameid "LookoutN";
		descid "LookoutD";
		
		cost_gold 350;
		cost_stone 200;
		
		gui_id DCSGUIID_ITOWN_BUILDING_6;		
	}
	building "pyromantium"
	{
		level 2;
		icon "Resources\Capitals\Alliance\icons\icon_elves_pyromantium.t";
		
		camera_track "Camera_Pyromantium";
		scene_group "Pyromantium";

		unit "elves_warden";
		unit_upgrade_from "elves_watchman";
		
		nameid "PyromantiumN";
		descid "PyromantiumD"; 
		
		cost_gold 1300;
		cost_stone 800;
		
		gui_id DCSGUIID_ITOWN_BUILDING_7;		
	}
	building "hydromantium"
	{
		level 2;
		icon "Resources\Capitals\Alliance\icons\icon_elves_hydromantium.t";
		
		camera_track "Camera_Hydromantium";
		scene_group "Hydromantium";

		unit "elves_sentry";
		unit_upgrade_from "elves_watchman";
		
		nameid "HydromantiumN";
		descid "HydromantiumD";
		
		cost_gold 1150;
		cost_stone 950;
		
		gui_id DCSGUIID_ITOWN_BUILDING_8;
	}
}
buildtree "mages"
{
buildtreeid "CapElvMag";
	building "elves mages default"
	{
		level 0;
		upgrade "cupola";
		default;
		
		camera_track "Camera_mages";
		scene_group "Mages_(default)";

		unit "elves_adept";
		gui_id DCSGUIID_ITOWN_BUILDING_0;		
	}
	building "cupola"
	{
		level 1;
		upgrade "elementarium";
		upgrade "high council";		
		//upgrade "aviary";
		icon "Resources\Capitals\Alliance\icons\icon_elves_cupola.t";
		
		camera_track "Camera_Cupola";
		scene_group "Cupola";

		unit "elves_channeler";
		unit_upgrade_from "elves_adept";
		
		nameid "CupolaN";
		descid "CupolaD";
		
		cost_gold 150;
		cost_stone 250;
		
		gui_id DCSGUIID_ITOWN_BUILDING_3;
	}
	building "elementarium"
	{
		level 2;
		icon "Resources\Capitals\Alliance\icons\icon_elves_elementarium.t";
		
		camera_track "Camera_Elementarium";
		scene_group "Elementarium";

		unit "elves_theurgist";
		unit_upgrade_from "elves_channeler";
		
		nameid "ElementariumN";
		descid "ElementariumD";
		
		
		cost_gold 650;
		cost_stone 1050;
		
		gui_id DCSGUIID_ITOWN_BUILDING_4;
	}
	building "high council"
	{	
		level 2;	
		icon "Resources\Capitals\Alliance\icons\icon_elves_highcouncil.t";
		
		camera_track "Camera_High_Counsil";
		scene_group "High_Counsel";

		unit "elves_archon";
		unit_upgrade_from "elves_channeler";
		
		nameid "HighCouncilN";
		descid	"HighCouncilD"; 
		
		cost_gold 900;
		cost_stone 1000;
		
		gui_id DCSGUIID_ITOWN_BUILDING_5;		
	}
	building "aviary"
	{
		level 1;
		nolock_tree;
		//requirements "cupola"
		upgrade "volary";
		icon "Resources\Capitals\Alliance\icons\icon_elves_aviary.t";
		
		camera_track "Camera_griffins";
		scene_group "Aviary";

		unit "elves_griffin";		

		nameid "AviaryN";
		descid "AviaryD";
		
		cost_gold 1500;
		cost_stone 600;
		
		gui_id DCSGUIID_ITOWN_BUILDING_1;
		
	}
	building "volary"
	{
		level 2;
		icon "Resources\Capitals\Alliance\icons\icon_elves_volary.t";
		
		camera_track "Camera_griffins";
		scene_group "Volary";

		unit "elves_skylord";
		unit_upgrade_from "elves_griffin";
		
		nameid "VolaryN";
		descid "VolaryD"; 
		
		cost_gold 2800;
		cost_stone 1400;
		
		gui_id DCSGUIID_ITOWN_BUILDING_2;		
	}
}

buildtree "support"
{
buildtreeid "CapElvSup";
	building "elves support default"
	{
		level 0;
		upgrade "spriggan circle";
		default;

		camera_track "Camera_healers";
		scene_group "Healers_default";

		unit "elves_spiritess";
		gui_id DCSGUIID_ITOWN_BUILDING_0;
	}
	building "spriggan circle"
	{
		level 1;
		upgrade "thicket";
		icon "Resources\Capitals\Alliance\icons\icon_elves_spriggancircle.t";

		camera_track "Camera_Spriggan_Circle";
		scene_group "Spriggan_Circle";

		unit "elves_oracle";
		unit_upgrade_from "elves_spiritess";
		
		nameid "SprigganCircleN";
		descid "SprigganCircleD"; 
		
		cost_gold 200;
		cost_stone 200;
		
		gui_id DCSGUIID_ITOWN_BUILDING_1;
	}
	building "thicket"
	{
		level 2;
		upgrade "firegrounds";
		upgrade "gallean's altar";
		icon "Resources\Capitals\Alliance\icons\icon_elves_thicket.t";

		camera_track "Camera_Thicket";
		scene_group "Thicket";

		unit "elves_grovemaiden";
		unit_upgrade_from "elves_oracle";
		
		nameid "ThicketN";
		descid "ThicketD"; 
		
		cost_gold 850;
		cost_stone 750;
		
		gui_id DCSGUIID_ITOWN_BUILDING_2;
	}
	building "firegrounds"
	{
		level 3;
		icon "Resources\Capitals\Alliance\icons\icon_elves_firegrounds.t";

		camera_track "Camera_Firegrounds";
		scene_group "Firegrounds";

		unit "elves_sundancer";
		unit_upgrade_from "elves_grovemaiden";
		
		nameid "FiregroundsN";
		descid "FiregroundsD";
		
		cost_gold 2250;
		cost_stone 1750;
		
		gui_id DCSGUIID_ITOWN_BUILDING_3;
	}
	building "gallean's altar"
	{
		level 3;
		icon "Resources\Capitals\Alliance\icons\icon_elves_galleansaltar.t";

		camera_track "Camera_Galleans_Altar";
		scene_group "Galleans_Altar";

		unit "elves_sylph";
		unit_upgrade_from "elves_grovemaiden";
		
		nameid "GalleanAltarN";
		descid "GalleanAltarD";
		
		
		cost_gold 1800;
		cost_stone 2200;
		
		gui_id DCSGUIID_ITOWN_BUILDING_4;
	}
}

buildtree "additional buildings"
{
buildtreeid "CapElvAdd";
	building "magic tower"
	{
		level 0;
		icon "Resources\Capitals\Alliance\icons\icon_elves_magetower.t";
		icon_big "Resources\Capitals\Alliance\icons\icon_elves_magetower_big.t";
		
		camera_track "Camera_mage_tower";
		scene_group "Mage_Tower";

		nameid "MagicTowerN";
		descid "MagicTowerD";
		
		cost_gold 400;
		cost_stone 150;
		gui_id DCSGUIID_ITOWN_BUILDING_1;
	}	
	building "temple"
	{
		level 0;
		icon "Resources\Capitals\Alliance\icons\icon_elves_temple.t";
		icon_big "Resources\Capitals\Alliance\icons\icon_elves_temple_big.t";

		camera_track "Camera_temple";
		scene_group "Temple";

		nameid "TempleN";
		descid "TempleD";
		
		cost_gold 250;
		cost_stone 250;

		gui_id DCSGUIID_ITOWN_BUILDING_2;
	}
	building "guild"
	{
		level 0;
		icon "Resources\Capitals\Alliance\icons\icon_elves_guild.t";
		icon_big "Resources\Capitals\Alliance\icons\icon_elves_guild_big.t";

		camera_track "Camera_guild";
		scene_group "Guild";
		
		unit "elves_leader-thief";

		nameid "GuildN";
		descid "GuildD";
		
		cost_gold 200;
		cost_stone 350;

		gui_id DCSGUIID_ITOWN_BUILDING_3;
	}

}
shop "Resources\\Profiles\\scripts\\capital_shop.s"